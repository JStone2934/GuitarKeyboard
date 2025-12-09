# GuitarKeyboard

用吉他进行键盘输入。通过录制拨弦进行按键绑定，在命中时发送对应的键盘输入。


## 环境准备
- Windows（默认在 `cmd.exe` 运行）
- Python 3.8+
- 麦克风可用（系统输入设备正常）

安装依赖：

```cmd
pip install numpy pyaudio pynput
```

注意：部分环境下 `pyaudio` 安装可能需要预编译轮子；如果安装失败，可搜索与你的 Python 版本匹配的 `whl` 文件后使用 `pip install <whl>` 安装。

## 文件结构
- `guitarKeyboard.py`：主程序，指纹录入、匹配与按键发送。
- `guitar_mapping.json`：音符编号到按键的映射（例如：`{"FP001": "space"}`）。
- `fingerprints.json`：指纹库（FFT 指纹向量与可选的键）。
- 重新录制映射可以手动删除两个JSON文件

## 工作原理简述
1. 触发：当音频能量突变超过阈值（`ONSET_THRESHOLD`）时触发识别或录入。
2. 指纹：采集多帧音频（可配 `--fp-frames`），计算窗口的归一化 FFT 作为指纹。
3. 比对：与库中每个指纹做余弦相似度，取最大者。
4. 判定：若相似度 ≥ `--sim-th` 且能量 ≥ `--min-energy`，认为命中，并发送对应按键。

## 快速上手

1) 交互式录入（自动编号，只需输入键名）

```cmd
python guitarKeyboard.py --build-map --fp-frames 6    
```

- 每次拨弦触发后：
	- 程序自动生成编号（如 `FP001`）。
	- 你只需输入键名（例如：`a`、`space`、`enter`、`1`）。
	- 指纹会保存到 `fingerprints.json`，键映射会同步到 `guitar_mapping.json`。
    - 和弦也可以录制，但是建议录制时将帧数调大

2) 运行匹配并发送按键

```cmd
python guitarKeyboard.py --use-map --sim-th 0.6 --min-energy 20000000 --fp-frames 6
```

- `--use-map`：命中时发送按键。
- `--sim-th`：相似度阈值（默认 0.9），越高越严格。
- `--min-energy`：能量阈值（默认 1e7），用于过滤弱信号。
- `--fp-frames`：指纹采集的连续帧数（默认 8）。录入与匹配尽量保持一致以提升稳定性。

3) 指定录入到某编号（可选）

```cmd
python guitarKeyboard.py --record-fp FP010 --record-key space --fp-frames 12
```

- 非交互地把当前触发指纹记录到指定编号，并绑定键名。

## 键名说明
- 普通字符：`a`、`b`、`1`、`!`（字符串会逐字符发送）。
- 特殊键：`space`、`enter`、`tab`、`esc`、`backspace`、`shift`、`ctrl`、`alt`、`up`、`down`、`left`、`right`、`delete`、`home`、`end`、`pageup`、`pagedown`。

## 参数与调优
- `ONSET_THRESHOLD`：能量突变触发阈值。变大更不敏感，变小更容易触发。
- `RESET_THRESHOLD`：复位阈值。变大更快恢复“待命”，避免余音影响。
- `--sim-th`：相似度阈值，推荐 0.9~0.95 起步。
- `--min-energy`：能量阈值，建议根据环境噪声与拾音强度调整。
- `--fp-frames`：指纹长度。适当增大有助稳定，但会提高延迟。

## 常见问题

- 指纹不稳定：
	- 提高 `--fp-frames`（如从 8 提到 12/16）。
	- 适当提高 `--sim-th` 与 `--min-energy`。
	- 录入时尽量靠近麦克风并保持一致的力度与位置。
- `pyaudio` 安装失败：使用与你的 Python 版本匹配的预编译 `whl`。



