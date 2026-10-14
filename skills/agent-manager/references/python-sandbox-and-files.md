# Python 节点沙箱与多模态文件变量

本页解释 **Python 节点运行时环境**、**文件变量在节点间的流转**，以及排查「LLM 看不到图片」的标准链路。
全部内容来自实测与源码核验（`app/agent_flow/node_handlers/python_handler.py`、`app/utils/media_resolver.py`），非推测。

---

## 1. 沙箱模块白名单（唯一事实源）

Python 节点运行在 **RestrictedPython** 沙箱中，运行时 `import` 按白名单放行，未授权模块直接抛
`ImportError: Module 'xxx' is not allowed for security reasons`，**不是** `pip install` 能解决的。

**可用模块（共 43 个）**：

```text
math json re datetime collections itertools functools decimal fractions typing
statistics hashlib uuid copy enum dataclasses string textwrap operator numbers
abc bisect heapq array struct base64 binascii io csv pathlib random time
calendar zoneinfo openpyxl docx pdfplumber pymupdf fitz urllib cryptography os requests
```

**明确不可用（高频踩坑）**：

| 模块 | 说明 | 替代方案 |
|---|---|---|
| `PIL` / `Pillow` | 被拦截 | `fitz`(pymupdf) 读图/画图/导出 |
| `numpy` | 被拦截 | 纯 Python 循环，或 `struct`/`array` |
| `importlib` | 被拦截 | 直接 `import` 白名单模块 |
| `sqlite3` | 被拦截 | 调后端 API（`requests`）或 shell 节点 |
| `subprocess` / `socket` / `ctypes` | 设计上排除 | 改用 `shell` / `ssh` 节点 |
| `glob` / `tempfile` / `shutil` / `sys` | 不在白名单 | `os.listdir` / `os.path` / `os.makedirs` |

> `os` 虽可用，但只有白名单内的能力可用（文件 IO、路径、环境变量、`os.makedirs` 等）。

## 2. 沙箱语法限制（AST 级，无法 try/except 绕过）

| 限制 | 报错 | 规避写法 |
|---|---|---|
| 变量名/函数名/类名 **不能以 `_` 开头** | `"xxx" is an invalid variable name because it starts with "_"` | 用 `to_ids()` 而非 `_to_ids()` |
| 属性名不能以 `_` 开头 | `"__name__" is an invalid attribute name...` | 不访问 `__name__`/`__doc__`/`__class__` |
| **不支持增量赋值 `+=` `-=` `*=`** | `NameError: name '_inplacevar_' is not defined` | 写 `cur = cur + x`；若 cur 是 list 用 `cur.append(x)` |
| 禁止 `__dunder__` 访问、`str.format()` 攻击面、`try/except*` | 编译期报错 `语法错误: ...` | — |

**注意**：`__save_file__` 作为 **dict 的字符串键** 是合法的（`{'__save_file__': True}`），因为那是字符串字面量而非标识符——不要和上表第 2 行混淆。

## 3. 沙箱运行环境

- **cwd = 项目根目录**（如 `D:\software\langgraph_agent`），因此 `uploads/`、`workspace/temp/` 可直接按相对路径读写，也可写绝对路径。
- **可访问网络**：`requests` 可直连本机后端 `http://localhost:8000/api`（实测 200），也用于外部 API 拉数据。
- **每次执行独立进程/线程**；无 `main` 时按脚本模式执行（顶层代码跑完 + stdout 返回），不报错。
- **输出包装**：节点输出不是 `main` 的裸返回值，而是
  `{"stdout","stderr","result","success"}`，所以访问 `main` 返回内容要走
  `nodes.<key>.result.result.<字段>`（**两个 `result`**）。
- **失败不影响落盘**：`main` 抛异常时 `success=false` 且 `stderr` 带堆栈，节点仍产出包装结构，下游可按需判断。

## 4. 多模态文件变量流转（核心链路）

### 4.1 流程图输入的 `file_list` 是**文件 ID**，不是路径

`input_schema` 里声明 `{"name":"files","type":"file_list"}` 后，节点内收到的是 **ID 列表**：

```json
{"files": [79]}
```

上游 python 节点拿到的也是 `[79]`（实测 `str(type(files))` → `<class 'list'>`）。
想要路径/原始名，必须自己查回来。

### 4.2 LLM 的 `file_inputs` 只认 file_info，不认裸 ID（最大坑）

`file_inputs` 的解析走 `media_resolver.normalize_file_value()`，它**只接受 dict**
（识别 `id`/`file_id`/`file_path`/`preview_url`/`name` 等键）。传入裸 `[79]` 时：

- 不报错、不告警，**静默丢弃**；
- 结果：模型收不到任何图片，只会回答「我没有看到图片」；
- 排查困难度极高的原因就在于此——**链路无任何错误信号**。

### 4.3 标准解法：加一个「解析文件信息」python 节点

在流程入口和 LLM 之间插入一个节点，把 ID 换成 file_info：

```python
import requests


def to_ids(files):
    ids = []
    if isinstance(files, list):
        for f in files:
            if isinstance(f, int):
                ids.append(f)
            elif isinstance(f, dict):
                fid = f.get('id') or f.get('file_id')
                if fid:
                    ids.append(int(fid))
            elif isinstance(f, str) and f.strip().isdigit():
                ids.append(int(f.strip()))
    elif isinstance(files, int):
        ids.append(files)
    return ids


def main(files):
    out = []
    for fid in to_ids(files):
        rec = {'id': fid}
        try:
            r = requests.post('http://localhost:8000/api/file/page',
                              json={'page': 1, 'page_size': 1,
                                    'condition': {'id': fid}},
                              timeout=30)
            for it in r.json().get('data', {}).get('items', []):
                if int(it.get('id')) == int(fid):
                    rec = it
                    break
        except Exception as e:
            rec['error'] = str(e)[:200]
        out.append(rec)
    return {'file_infos': out}
```

然后 LLM 节点的 `file_inputs` 引用它：

```json
{
  "file_inputs": ["nodes.file_resolve.result.result.file_infos"],
  "capabilities": {"image": true, "video": false, "audio": false, "pdf": false}
}
```

### 4.4 必要条件清单（缺一不可）

| 条件 | 说明 |
|---|---|
| `capabilities.<模态> = true` | 节点级开关；**且**模型本身要支持该模态 |
| 适配器支持该模态 | `openai_compatible` 支持 image/audio/pdf；`anthropic` 支持 image/pdf。**video 两者都未实现**，会被降级为文本占位 |
| 文件是 dict | 见 4.2；不能是裸 ID/裸路径字符串 |
| 文件路径可读 | `file_path` 为相对路径时会自动转绝对路径；不存在/超限（`MAX_FILE_SIZE`）降级为文本标注 |

> 适配器与模型能力是**两个独立维度**，取交集后才注入。设了 `image:true` 但模型不支持，会得到文本占位而非报错。

## 5. 文件相关 API 速查

| 用途 | 接口 |
|---|---|
| 按条件查文件（含 `file_path`/`download_url`） | `POST /api/file/page`，body `{"page":1,"page_size":10,"condition":{"id":79}}` |
| 下载/预览文件 | `GET /api/file/download/{file_id}`（`?inline=true` 浏览器内联） |
| 上传 | `POST /api/file/upload`（multipart，字段 `file`） |
| 删除 | `GET /api/file/delete/{file_id}` |

`file/page` 的 `condition` 支持 `id`（靠 `BaseView` 的基类字段）、`source_type`、`flow_id`、`original_name`、`mime_type`。

**排查建议**：接口路径优先读源码 `app/api/file_api.py`，或从打包前端 `_internal/frontend/dist/assets/*.js` 中检索
`"/file/"` 字面量；**不要靠猜端点**（`/api/file/{id}`、`/api/files/{id}`、`/api/file/detail/{id}` 全部 404）。

## 6. 典型场景：图片目标检测 + 绘框（端到端范式）

沙箱内用 `fitz` 实现「读原图 → 比例换算 → 画框 → 导出」的完整写法。
**关键点**：`fitz` 不能在图片页上直接 `draw_rect`（抛 `ValueError: is no PDF`），必须走"新建 PDF 页 + 贴图 + 绘制"。

```python
import base64
import os
import requests
import fitz


def parse_size(text):
    """从 '1240*788' / '1240x788' 等文本提取 (宽, 高)"""
    nums = []
    cur = []
    for ch in str(text):
        if ch.isdigit():
            cur.append(ch)
        else:
            if cur:
                nums.append(int(''.join(cur)))
                cur = []
    if cur:
        nums.append(int(''.join(cur)))
    if len(nums) >= 2:
        return nums[0], nums[1]
    return 0, 0


def main(files, img_size, objects):
    fid = int(files[0]) if isinstance(files, list) else int(files)

    # 1) 原图字节（从本机 API 取）
    raw = requests.get('http://localhost:8000/api/file/download/%d' % fid,
                       timeout=120).content

    # 2) 原图真实像素尺寸
    doc = fitz.open(stream=raw, filetype='png')
    rect = doc.load_page(0).rect
    ow = int(round(rect.x1))
    oh = int(round(rect.y1))

    # 3) 比例 = 原图尺寸 / AI 实际看到的尺寸（模型常压缩/缩放图片）
    ai_w, ai_h = parse_size(img_size)
    if ai_w <= 0 or ai_h <= 0:
        ai_w, ai_h = ow, oh
    rx = float(ow) / float(ai_w)
    ry = float(oh) / float(ai_h)

    # 4) 新建 PDF 页贴原图，再按比例画框（图片页不能直接绘制）
    out_doc = fitz.open()
    page = out_doc.new_page(width=ow, height=oh)
    page.insert_image(page.rect, stream=raw)

    lw = max(2, int(round(ow / 300.0)))
    fs = max(12, int(round(ow / 60.0)))
    for b in objects:
        x1 = min(b['x1'], b['x2']) * rx
        y1 = min(b['y1'], b['y2']) * ry
        x2 = max(b['x1'], b['x2']) * rx
        y2 = max(b['y1'], b['y2']) * ry
        page.draw_rect(fitz.Rect(x1, y1, x2, y2), color=(1, 0, 0), width=lw)
        if b.get('name'):
            # 'china-s' 内置简体中文字体，缺省字体画中文会报错
            page.insert_text(fitz.Point(x1, max(fs, y1 - 4)), b['name'],
                             fontsize=fs, color=(1, 0, 0), fontname='china-s')

    # 5) dpi=72 导出 → 输出尺寸与原图一致
    png = page.get_pixmap(dpi=72).tobytes('png')

    # 6) 落盘（生产路径会用 __save_file__ 再保存进文件管理）
    temp_dir = 'workspace/temp/bbox_draw'
    if not os.path.isdir(temp_dir):
        os.makedirs(temp_dir)
    out_path = os.path.join(temp_dir, 'annotated_%d.png' % fid)
    with open(out_path, 'wb') as fh:
        fh.write(png)
    return {'success': True, 'png_path': out_path}
```

**要点回顾**：

1. `fitz.open(stream=raw, filetype='png')` 读图 → `page.rect` 拿真实尺寸。
2. **比例校正**必须做：模型报的尺寸常与原图不同（实测 934×590 被报成 1240×788），直接画会偏移。
3. 绘制必须"新建 PDF 页 + `insert_image` + `draw_rect`"，不能在图片页上画。
4. 中文标签用 `fontname='china-s'`。
5. `get_pixmap(dpi=72)` 保证输出与原图像素一一对应。

### 保存产出文件

`main` 返回 `__save_file__` 结构，生产路径会落盘到 `uploads/` 并写入文件管理：

```python
def main(png_path, out_name):
    with open(png_path, 'rb') as fh:
        data = fh.read()
    return {
        '__save_file__': True,
        'content_base64': base64.b64encode(data).decode(),
        'mime_type': 'image/png',
        'filename': out_name or 'result.png',
    }
```

保存后 `preview_url`、`download_url`、`file_name`、`file_id` 会**提升到执行包装顶层**，
可直接用 `nodes.<key>.result.download_url` 引用。

## 7. 试运行（不跑全流程验证代码）

```text
POST /api/debug/python
{"code": "...", "timeout": 30, "input_data": {...}, "debug_session_id": "uuid"}
```

- 与生产路径**共享沙箱核心**（同 `_run_python_in_sandbox`），编译限制、白名单、超时一致。
- **副作用隔离**：不写 File DB、不做文件追踪；`__save_file__` 落盘到
  `workspace/temp/debug_uploads/<debug_session_id>/`，7 天后自动清理。
- 适合纯逻辑调试；但 `input_data` 必须显式传值，**不会注入流程上下文**——要验证节点变量解析仍需真跑一遍流程。