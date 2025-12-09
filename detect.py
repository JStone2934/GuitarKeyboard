import pyaudio
import numpy as np
import time
import sys
import json
import os
import argparse
try:
    from pynput.keyboard import Controller, Key
    KEYBOARD_AVAILABLE = True
    _kb = Controller()
except Exception:
    KEYBOARD_AVAILABLE = False
    _kb = None

# --- 🎛️ 核心参数调试区 ---

# 1. 触发阈值 (ONSET): 只有能量突变量超过这个值，才判定为拨弦
#    调大 = 需要更用力拨弦才能触发
#    调小 = 更灵敏
ONSET_THRESHOLD = 2e8

# 2. 复位阈值 (RESET): 只有当前总能量低于这个值，系统才会“解锁”，准备下一次识别
#    这个值必须比拨弦时的能量低很多，但要比背景噪音高
#    如果你的弦一直在响导致无法识别下一次，请调大这个值
RESET_THRESHOLD = 8e7

# --- 其他参数 ---
CHUNK = 2048              
FORMAT = pyaudio.paInt16  
CHANNELS = 1              
RATE = 44100              

# 键盘映射文件路径
MAPPING_FILE = os.path.join(os.path.dirname(__file__), "guitar_mapping.json")
FINGERPRINT_FILE = os.path.join(os.path.dirname(__file__), "fingerprints.json")

def load_mapping():
    if not os.path.exists(MAPPING_FILE):
        return {}
    try:
        with open(MAPPING_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_mapping(mapping):
    try:
        with open(MAPPING_FILE, "w", encoding="utf-8") as f:
            json.dump(mapping, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False

def load_fingerprints():
    if not os.path.exists(FINGERPRINT_FILE):
        return {}
    try:
        with open(FINGERPRINT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_fingerprints(db):
    try:
        with open(FINGERPRINT_FILE, "w", encoding="utf-8") as f:
            json.dump(db, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False

def _parse_key_name(name: str):
    """将字符串键名映射到 pynput 的 Key 或直接字符。
    支持示例：a, b, 1, space, enter, tab, esc, backspace, shift, ctrl, alt, up, down, left, right.
    未匹配到特殊键时，返回原字符序列逐个输入。
    """
    if not name:
        return None
    lower = name.lower()
    special_map = {
        "space": Key.space,
        "enter": Key.enter,
        "return": Key.enter,
        "tab": Key.tab,
        "esc": Key.esc,
        "escape": Key.esc,
        "backspace": Key.backspace,
        "shift": Key.shift,
        "ctrl": Key.ctrl,
        "control": Key.ctrl,
        "alt": Key.alt,
        "up": Key.up,
        "down": Key.down,
        "left": Key.left,
        "right": Key.right,
        "delete": Key.delete,
        "home": Key.home,
        "end": Key.end,
        "pageup": Key.page_up,
        "pagedown": Key.page_down,
    }
    return special_map.get(lower, name)

def send_keypress(key_name: str):
    target = _parse_key_name(key_name)
    if target is None or _kb is None:
        raise RuntimeError("pynput 不可用或键名为空")
    if isinstance(target, Key):
        _kb.press(target)
        _kb.release(target)
    elif isinstance(target, str):
        for ch in target:
            _kb.press(ch)
            _kb.release(ch)
    else:
        _kb.press(str(target))
        _kb.release(str(target))

# 旧的音高识别算法已移除，采用指纹匹配替代

def fft_fingerprint(signal):
    windowed = signal * np.hanning(len(signal))
    spectrum = np.abs(np.fft.rfft(windowed))
    # 归一化为单位向量以用于余弦相似度
    norm = np.linalg.norm(spectrum)
    if norm == 0:
        return spectrum.tolist()
    return (spectrum / norm).tolist()

def cosine_similarity(vec_a, vec_b):
    # 两向量需同长度；若不同，截断为最短长度
    n = min(len(vec_a), len(vec_b))
    if n == 0:
        return 0.0
    a = np.array(vec_a[:n])
    b = np.array(vec_b[:n])
    denom = (np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)

def draw_bar(energy, is_ready):
    """在控制台绘制能量条，方便调试"""
    # 将能量对数化以便显示
    if energy < 1: energy = 1
    log_energy = np.log10(energy)
    
    # 简单的缩放映射，根据你的麦克风可能需要调整 range
    bar_len = int((log_energy - 4) * 10) 
    if bar_len < 0: bar_len = 0
    if bar_len > 50: bar_len = 50
    
    bar = "█" * bar_len + "░" * (50 - bar_len)
    
    status = "🟢 待命 (Ready)" if is_ready else "🔴 锁定 (Locked)"
    sys.stdout.write(f"\r能量: [{bar}] {int(energy)} | {status}")
    sys.stdout.flush()

def run_detector(use_map=False, record_note=None, record_key=None, sim_threshold=0.9, min_energy=1e7, build_map=False):
    p = pyaudio.PyAudio()
    stream = p.open(format=FORMAT, channels=CHANNELS, rate=RATE, input=True, frames_per_buffer=CHUNK)

    print("\n🎸 严谨防抖模式启动")
    print(f"触发阈值: {ONSET_THRESHOLD} | 复位阈值: {RESET_THRESHOLD}")
    mode = "交互建表" if build_map else ("指纹录入" if record_note else "指纹比对")
    print(f"模式: {mode}")
    print("---------------------------------------------------")

    prev_energy = 0
    
    # 核心状态标志：是否准备好接受下一次拨弦
    # True = 安静等待中
    # False = 刚刚拨过，正在等待琴弦静止
    is_ready_to_trigger = True 

    mapping = load_mapping()
    fingerprints = load_fingerprints()

    if use_map and not KEYBOARD_AVAILABLE:
        print("⚠️ 未能导入 pynput 库，无法发送键盘输入。请先安装: pip install pynput")

    try:
        while True:
            raw_data = stream.read(CHUNK, exception_on_overflow=False)
            data_int = np.frombuffer(raw_data, dtype=np.int16)
            
            # 计算绝对总能量
            curr_energy = np.sum(data_int.astype(float)**2)
            
            # 计算能量突变量 (Flux)
            flux = curr_energy - prev_energy
            prev_energy = curr_energy 

            # --- 状态机逻辑 ---

            if is_ready_to_trigger:
                # 状态 1: 待命模式
                # 只有能量突然暴涨 (Flux > 阈值) 才会触发
                if flux > ONSET_THRESHOLD:
                    
                    # 🎯 触发识别！
                    # 换行打印以免破坏进度条显示
                    sys.stdout.write("\n") 
                    if build_map:
                        # 交互式建表：为当前触发的 FFT 指纹绑定 NOTE 与 KEY
                        fp = fft_fingerprint(data_int.astype(float))
                        try:
                            note_input = input("请输入音符名(如 G3、F#3)：").strip()
                        except EOFError:
                            note_input = ""
                        if not note_input:
                            # 未输入音符名则自动生成编号，如 FP001、FP002...
                            idx = max(
                                [
                                    int(k[2:]) for k in fingerprints.keys()
                                    if isinstance(k, str) and k.startswith("FP") and k[2:].isdigit()
                                ]
                            , default=0) + 1
                            note_input = f"FP{idx:03d}"
                            print(f"ℹ️ 未输入音符名，已使用默认编号: {note_input}")
                        if note_input:
                            try:
                                key_input = input("请输入要绑定的键(如 a/space/enter/1 等)：").strip()
                            except EOFError:
                                key_input = ""
                            # 写入指纹库
                            fingerprints[note_input] = {
                                "fingerprint": fp,
                                "key": key_input
                            }
                            ok_fp = save_fingerprints(fingerprints)
                            # 同步到映射文件（可选，作为回退）
                            if key_input:
                                mapping[note_input] = key_input
                                ok_map = save_mapping(mapping)
                            else:
                                ok_map = True
                            status_fp = "✅ 指纹已保存" if ok_fp else "❌ 指纹保存失败"
                            status_map = "✅ 映射已保存" if ok_map else "❌ 映射保存失败"
                            print(f"{status_fp} 到 {FINGERPRINT_FILE}；{status_map} 到 {MAPPING_FILE}")
                        else:
                            print("ℹ️ 未输入音符名，已跳过本次录入。")
                    elif record_note:
                        # 指纹录入模式：保存当前 FFT 指纹到库
                        fp = fft_fingerprint(data_int.astype(float))
                        fingerprints[record_note] = {
                            "fingerprint": fp,
                            "key": record_key or mapping.get(record_note, "")
                        }
                        if save_fingerprints(fingerprints):
                            print(f"✅ 已录入指纹: {record_note} -> {len(fp)} 维，键: {fingerprints[record_note]['key']}，文件: {FINGERPRINT_FILE}")
                        else:
                            print(f"❌ 指纹写入失败: {FINGERPRINT_FILE}")
                    else:
                        # 指纹比对模式（默认）
                        fp_cur = fft_fingerprint(data_int.astype(float))
                        best_note = None
                        best_sim = 0.0
                        for n, item in fingerprints.items():
                            sim = cosine_similarity(fp_cur, item.get("fingerprint", []))
                            if sim > best_sim:
                                best_sim = sim
                                best_note = n
                        energy = np.sum(data_int.astype(float)**2)
                        print(f"🔎 最相似: {best_note} | 相似度: {best_sim:.3f}")
                        if best_note and best_sim >= sim_threshold and energy >= min_energy:
                            key = fingerprints.get(best_note, {}).get("key", mapping.get(best_note, ""))
                            if key:
                                print(f"🚀 指纹命中: {best_note} -> {key} | sim={best_sim:.3f}")
                                if use_map and KEYBOARD_AVAILABLE:
                                    try:
                                        send_keypress(key)
                                        print("⌨️ 已发送键盘输入 (pynput)。")
                                    except Exception as e:
                                        print(f"⚠️ 发送键盘输入失败: {e}")
                            else:
                                print(f"   指纹命中: {best_note}，但无键映射。")
                        else:
                            print("   未达到相似度或能量阈值。")
                    
                    # 🔒 立即锁死系统
                    is_ready_to_trigger = False
            
            else:
                # 状态 2: 锁定模式 (Reseting)
                # 在这个模式下，无论怎么拨弦，程序都不理会
                # 只有当绝对能量 (curr_energy) 降到非常低 (RESET_THRESHOLD) 时
                # 才重新把系统设为 "Ready"
                
                if curr_energy < RESET_THRESHOLD:
                    is_ready_to_trigger = True
                    # sys.stdout.write("\n🔄 系统复位，准备下次拨弦...\n") # 调试用，嫌吵可注释

            # 绘制实时能量条 (可选，会降低一点点性能)
            draw_bar(curr_energy, is_ready_to_trigger)

    except KeyboardInterrupt:
        print("\n停止。")
    finally:
        stream.stop_stream()
        stream.close()
        p.terminate()

def main():
    parser = argparse.ArgumentParser(description="吉他指纹识别 + 键盘触发")
    parser.add_argument("--use-map", action="store_true", help="指纹命中时发送键盘输入")
    parser.add_argument("--build-map", action="store_true", help="连续交互式建表：每次触发后录入音符与按键")
    parser.add_argument("--record-fp", metavar="NOTE", help="指纹录入：当触发时录入当前 FFT 为指定音符的模板")
    parser.add_argument("--record-key", metavar="KEY", help="与 --record-fp 一起使用，指定该音符的键映射")
    parser.add_argument("--sim-th", type=float, default=0.9, help="指纹相似度阈值，默认 0.9")
    parser.add_argument("--min-energy", type=float, default=1e7, help="能量阈值，默认 1e7")
    args = parser.parse_args()

    run_detector(
        use_map=args.use_map,
        record_note=args.record_fp,
        record_key=args.record_key,
        sim_threshold=args.sim_th,
        min_energy=args.min_energy,
        build_map=args.build_map,
    )

if __name__ == "__main__":
    main()