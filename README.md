# M2-D / M2-F 视频二创工作流

这是一个只发布两条生产路线的公共仓库：

- `M2_D_SHARE_FIRST`：保留源事实和语义，改写成更适合转发的后期旁白，并重新生成画面。
- `M2_F_SOURCE_AUDIO_RESTYLE`：完整保留原 MP3、逐字文案、语言和时间轴，只重新设计画面。

其他内容路线没有包含在这个仓库中，也不能通过修改标签绕过路由限制。

## 当前版本

- 发布版本：`R6.41.2-DF1-PUBLIC.1`
- 上游生产核心：`R6.41`
- 上游修订：`R6.41.2`
- 默认画风：`DOG_HIGH_SHARE_MONO_COMIC`
- ImageGen：整张宫格；内置工具使用 `FLEXIBLE_REFERENCE`
- 视频：Grok/KIE 宫格优先、确定性首格第二
- 验收：单段不低于80分、全片平均不低于85分、硬错误为0

## 下载与验证

```powershell
git clone https://github.com/wangyueqing123/video-remake-m2d-m2f-workflow-public.git
Set-Location video-remake-m2d-m2f-workflow-public
git checkout R6.41.2-DF1-PUBLIC.1
python -X utf8 -B scripts\validate_distribution.py
```

验证结果必须为 `PASSED`。失败时停止，不得跳过。

## 环境

Windows 基础软件：

- Git；
- Python 3.11–3.13；
- Tesseract OCR 5.x，以及 `chi_sim`、`eng` 语言包；
- Codex（需要内置 ImageGen）；
- 剪映专业版；
- KIE/Grok API 账号；
- `jianying-ai-foundation`，仅在生成剪映草稿时作为只读依赖。

先确认基础命令可以使用：

```powershell
git --version
python --version
tesseract --version
```

在仓库根目录创建独立 Python 环境并安装媒体依赖：

```powershell
python -m venv .venv
Set-ExecutionPolicy -Scope Process Bypass
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -X utf8 -B scripts\check_r62_environment.py
python -X utf8 -B scripts\validate_distribution.py
```

如果系统不能直接找到 Tesseract，可在执行 OCR 时通过 `--tessdata-dir` 指向包含语言包的目录。运行下面命令应能看到 `chi_sim` 与 `eng`：

```powershell
tesseract --list-langs
```

创建 KIE 视频任务前，只在当前 PowerShell 进程设置密钥：

```powershell
$env:KIE_API_KEY = "<本机KIE密钥>"
```

测试阶段默认使用480p。任何 ImageGen、资产上传或视频任务都必须先完成人工审批封印；每个任务只能提交一次，不自动重试。

默认不会调用外部 TTS API。任何密钥只放在本机环境变量或仓库外的私密文件中，不能提交到 Git。

## 必需素材

两种模式都需要：

- 源 MP4；
- 可读取的源音频或独立 MP3；
- 可靠转录；
- 可选封面 JPG；
- 目标账号、人物、动物、环境、画风和比例要求。

M2-F 额外要求原 MP3 与逐字稿完全绑定；M2-D 额外要求明确允许忠实改写为转发型旁白。

## 标准使用命令：M2-D

把下面整段交给 Codex，并替换素材路径：

```text
使用 $video-remake-m2d-m2f 执行 M2_D_SHARE_FIRST。
源视频：<绝对路径.mp4>
源音频：<绝对路径.mp3，可由视频读取时注明>
封面：<可选绝对路径.jpg>

保持源语言，完整继承源语义单元、事实、条件、因果和结论；允许改写成转发优先的自然旁白，但不得增加无依据事实。默认使用 DOG_HIGH_SHARE_MONO_COMIC；如果我另行指定风格，先在P1锁定风格合同。

声音使用后期旁白，默认剪映“真人播客女”1.3倍；最终实测旁白是唯一时间权威。按完整大动作场景分段，一段一宫格，整张宫格生成。自主完成P1-P5；任何ImageGen、上传或视频任务必须先给出封印和标准批准命令，批准后只提交一次，不自动重试。最终生成剪映草稿、字幕和QC报告。
```

## 标准使用命令：M2-F

```text
使用 $video-remake-m2d-m2f 执行 M2_F_SOURCE_AUDIO_RESTYLE。
源视频：<绝对路径.mp4>
原始MP3：<绝对路径.mp3>
封面：<可选绝对路径.jpg>

原MP3完整保留，速度1.0，不裁切、不换声、不翻译；逐字文案不改写、不增删、不重排。源视频只提供大场景动作语义，不把源关键帧或源宫格作为ImageGen输入。默认使用 DOG_HIGH_SHARE_MONO_COMIC；如果我另行指定风格，先在P1锁定风格合同。

按原声时间轴规划完整大动作场景，一段一宫格，整张宫格生成。视频模型音轨全部静音，最终画面服从原MP3。自主完成P1-P5；任何ImageGen、上传或视频任务必须先给出封印和标准批准命令，批准后只提交一次，不自动重试。最终生成使用原MP3、逐字字幕的剪映草稿和QC报告。
```

## 画风设置

画风与模式完全分离。未指定时使用黑白粗线条宠物漫画风：

- `DOG_HIGH_SHARE_MONO_COMIC`：默认黑白/中性灰，只有登记的局部强调色。
- `DOG_STYLE_C_GHIBLI_PET_NARRATIVE`：温暖日式手绘二维生活叙事，不复刻具体影视画面。
- `DOG_STYLE_D_INDOOR_CARE_KEYFRAME`：日式二维轻写实室内照护，强调动作、工具和接触关系。
- `DOG_STYLE_E_REACTION_RESONANCE`：日式二维轻拟人反应共鸣，强调触发与反应。
- `CUSTOM_NAMED_STYLE`：用户提供完整自定义风格合同。

切换画风必须在P1完成，并新建项目修订。换画风不能改变模式、文案、语言、声音、动作因果和时间轴。

## 成本纪律

- 先审核证据、场景、Prompt和引用图，再调用模型。
- 一次生成整张宫格，禁止逐格生成或逐格修复。
- 每张宫格最多一次基线和一次合并修正。
- 每次上传和视频任务分别审批，只允许提交一次。
- 不自动重试，不因为“可能更准”增加调用。
- 供应商整数秒只影响请求；最终剪辑长度服从旁白或原MP3。

## 产物

每个项目最终应保留：

- P1–P9阶段材料与哈希血缘；
- 宫格、确定性首格和项目视觉锚点；
- ImageGen、上传和视频调用封印；
- 视频段QC与总片评分；
- 旁白或原MP3时间轴；
- 字幕轨；
- 可直接打开的剪映/CapCut草稿；
- 最终完成回执。
