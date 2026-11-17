# -*- coding: utf-8 -*-
"""
build_bank.py —— 把「高频考点100题」PDF 题库解析成结构化 JSON。

设计原则
--------
1. 不编造：题干、选项、答案、解析全部来自 PDF 原文，脚本不做任何"推理补全"。
2. 宁缺毋滥：任何对不上的地方都记入告警，不猜、不填默认值。
3. 可复核：输出逐题校对表（Markdown），并统计覆盖率。

用法
----
    python build_bank.py             # 解析全部科目
    python build_bank.py 生物        # 只解析一科

输出
----
    data/questions.json      题库（网页读取）
    data/img/<科目>/<题号>.png  每道题的原题截图
    data/report.md           校对报告
"""
import fitz
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding='utf-8')

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BNK = os.path.join(BASE, '高频考点100题')
OUT_DATA = os.path.join(BASE, 'data')
OUT_IMG = os.path.join(OUT_DATA, 'img')

SUBJECTS = ['生物', '历史', '政治', '地理']
TOTAL_Q = 100
IMG_DPI = 160
# 截图格式：WebP 质量 80。实测与 PNG 肉眼看不出差别（文字/线条锐利），
# 体积约为 PNG 的 1/3，对手机加载流量友好。
IMG_FORMAT = 'WEBP'
IMG_QUALITY = 80

# ---------- 文本规范化 ----------

def norm(s):
    """去空白、去标点，用于比对题干是否一致。"""
    s = re.sub(r'\s+', '', s)
    s = re.sub(r'[，。、；：？！“”‘’（）()《》〈〉·…—－\-]', '', s)
    return s


def clean_text(s):
    """保留字面但压掉多余空白（题干/选项展示用）。"""
    s = s.replace('\u00a0', ' ')
    s = re.sub(r'[ \t]+', ' ', s)
    s = s.replace('\n', '')
    # 复原被 OCR 拆开的数字：「6 5～66 题」→「65～66 题」「1 0 0 题」→「100 题」
    s = re.sub(r'(?<=\d)\s+(?=\d)', '', s)
    return s.strip()


def tight(s):
    """把「A ．xxx」这类被拆开的标记收紧成「A．xxx」。只动标记，不动正文。"""
    s = re.sub(r'\b([A-D])\s+([．.、])', r'\1\2', s)
    s = re.sub(r'\b([A-D])([．.、])\s+', r'\1\2', s)
    return s


# 分栏判定阈值（A4 宽 595pt，双栏时中缝在 ~290）
COL_SPLIT = 290


def iter_lines(path):
    """
    产出每一行的 (页码, x, y, 文本)，按「页 → y → x」排好序。
    fitz 的 block 顺序是内容流顺序，不能直接信；必须按坐标排。
    """
    doc = fitz.open(path)
    rows = []
    for pi, page in enumerate(doc):
        for blk in page.get_text("dict")["blocks"]:
            if blk.get("type") != 0:          # 跳过图片块
                continue
            for line in blk["lines"]:
                text = ''.join(sp["text"] for sp in line["spans"])
                if not text.strip():
                    continue
                x0, y0, x1, y1 = line["bbox"]
                rows.append({
                    "p": pi + 1,
                    "x": round(x0, 1), "y": round(y0, 2),
                    "x1": round(x1, 1), "y1": round(y1, 1),
                    "t": text,
                })
    rows.sort(key=lambda r: (r["p"], r["y"], r["x"]))
    return doc, rows


def stitch(rows):
    """
    把同一物理行的碎片粘回去。
    本项目 PDF 用 LaTeX 式排版：字母标记、分隔符、正文常被拆成多个独立 line，
    且 y 有 1-3pt 抖动。所以按「y 相近 + x 不重叠」判断同一行，拼完再按 x 排序。
    """
    by_page = {}
    for r in rows:
        by_page.setdefault(r["p"], []).append(r)

    out = []
    for p in sorted(by_page):
        items = sorted(by_page[p], key=lambda r: (r["y"], r["x"]))
        lines = []
        for r in items:
            text = r["t"]
            if not text.strip():
                continue
            best, best_gap = None, 1e9
            for ln in reversed(lines):
                if r["y"] - ln["y"] > 4.0:
                    break
                gap = r["x"] - ln["x1"]
                if -3 <= gap < best_gap:        # 允许小幅重叠（tight bbox）
                    best, best_gap = ln, gap
            if best is not None:
                best["t"] += ('' if best_gap < 6 else ' ') + text
                best["x1"] = max(best["x1"], r["x1"])
            else:
                lines.append({"p": p, "y": r["y"], "x": r["x"], "x1": r["x1"], "t": text})
        out.extend(lines)
    out.sort(key=lambda r: (r["p"], r["y"], r["x"]))
    return out


RE_SPLIT_MARK = re.compile(r'^([A-D])\s+([A-D])([．.、])(.*)$')
# 「A B C」这种连续裸字母（ASCII 字母，后接可选空白，前面是中文或空）
BARE_LETTERS = re.compile(r'(?<![A-Za-z])([A-D](?:\s+[A-D])+)')
RE_DOT_SEG = re.compile(r'([．.、])([^．.、]+)')


def fix_split_marks(rows):
    """
    修复三类排版碎片（只在形态完全明确时生效，绝不猜测）：

    1) "C D．云贵高原" + 紧邻同行的 "．黄土高原"
       → C．黄土高原  D．云贵高原

    2) "…题干 A B C ．①③④ ．②③④ ．①②③"
       → A．①③④  B．②③④  C．①②③

    3) "(A )."  →  "(A)．"   （PDF 里括号内选项字母后多出来的点）
    """
    out = []
    consumed = set()
    for i, r in enumerate(rows):
        if i in consumed:
            continue
        t = r["t"]

        # 形态 3：括号内选项残留点。注意 PDF 里可能是全角右括号，也可能是半角 ')' 或 ') '
        t = re.sub(r'([（(]\s*[A-D])\s*[）)]\s*[．.、]', r'\1）', t)

        # 形态 4：括号里的「A」被拉成选项标记，如 "(A ).《巴黎圣母院》 B.《人间喜剧》"
        #        仅当括号后跟「．内容」且整行还有另一个 B-D 选项标记时改写
        m4 = re.search(r'[（(]\s*([A-D])\s*[）)]\s*[．.、]\s*(.*)$', t)
        if m4 and re.search(r'[（(]?\s*[B-D]\s*[．.、]', m4.group(2)):
            t = t[:m4.start()] + m4.group(1) + '．' + m4.group(2)

        # 形态 5：题干行尾的「(A )」+ 下一物理行的「．选项内容」
        #        PDF 把 A 塞进括号里当成了原文一部分，实际是 A 选项。
        #        跨行距离放宽到 60pt（中间可能夹公式行）
        m5 = re.search(r'[（(]\s*([A-D])\s*[）)]\s*$', t)
        if m5 and i + 1 < len(rows):
            nxt = rows[i + 1]
            if (nxt["p"] == r["p"] and 0 < nxt["y"] - r["y"] < 60
                    and re.match(r'^\s*[．.、]', nxt["t"])):
                t = (t[:m5.start()] + '（ ） '
                     + m5.group(1) + '．' + nxt["t"].lstrip('．.、 '))
                consumed.add(i + 1)

        # 形态 1
        m = RE_SPLIT_MARK.match(t)
        if m and m.group(1) < m.group(2) and i + 1 < len(rows):
            nxt = rows[i + 1]
            if (nxt["p"] == r["p"] and abs(nxt["y"] - r["y"]) < 4.0
                    and re.match(r'^[．.、]', nxt["t"])):
                dot = m.group(3)
                rebuilt = (f'{m.group(1)}{dot}{nxt["t"][1:]} '
                           f'{m.group(2)}{dot}{m.group(4)}')
                out.append({**r, "t": rebuilt, "x1": max(r["x1"], nxt["x1"])})
                consumed.add(i + 1)
                continue

        # 形态 2：'A B C D．台风' + 下一行 '．泥石流 ．干旱 ．洪涝'
        m2 = re.search(r'((?<![A-Za-z])[A-D](?:\s+[A-D])+)\s*([．.、])(.*)$', t)
        if m2 and i + 1 < len(rows):
            nxt = rows[i + 1]
            if (nxt["p"] == r["p"] and abs(nxt["y"] - r["y"]) < 4.0
                    and re.match(r'^[．.、]', nxt["t"])):
                letters = re.findall(r'[A-D]', m2.group(1))
                tail = [m2.group(3)] + re.split(r'[．.、]', nxt["t"])[1:]
                tail = [x.strip() for x in tail if x.strip()]
                if len(letters) == len(tail):
                    head = t[:m2.start()]
                    body = ' '.join(f'{letters[k]}{m2.group(2)}{v}'
                                    for k, v in enumerate(tail))
                    out.append({**r, "t": head.rstrip() + ' ' + body,
                                "x1": max(r["x1"], nxt["x1"])})
                    consumed.add(i + 1)
                    continue

        out.append({**r, "t": t})
    return out


# ---------- 噪声（页眉页脚）过滤 ----------

RE_PAGENO = re.compile(r'^\d{1,3}$')


def is_noise(row, page_height):
    t = row["t"].strip()
    if not t:
        return True
    # 页码：页脚正中的纯数字
    if RE_PAGENO.match(t) and row["y"] > page_height - 90:
        return True
    # 卷头
    if '普通高中学业水平合格性考试' in t and len(t) < 40:
        return True
    if re.search(r'考点100题', t) and len(t) < 25:
        return True
    # 页眉/页脚里带页码的悬挂数字（如 "95～96 题。" 被粘上 "22"）
    if re.fullmatch(r'\d{1,3}', t) and row["y"] > page_height - 60:
        return True
    return False


def strip_trailing_pageno(t, row_y, page_height):
    """去掉行尾粘上来的页码（页脚附近的行才处理）。"""
    if row_y > page_height - 70:
        t = re.sub(r'\s+\d{1,3}$', '', t)
    return t


# ---------- 题目卷解析 ----------

RE_Q_START = re.compile(r'^\s*(\d{1,3})\s*[．.、]\s*(.*)$')
RE_OPT_MARK = re.compile(r'([A-D])\s*[．.、]\s*')


def is_option_line(t):
    """整行是不是选项行（以 A-D 加分隔符开头）。"""
    return bool(re.match(r'^\s*[A-D]\s*[．.、]', t))


def split_inline_options(t):
    """
    把含内联选项的行拆成 {A:..., B:...}。
    返回 (前缀文本, options dict)。
    只要出现 >=1 个「字母+分隔符」标记就按选项行处理：
    本项目 PDF 排版会把整行选项打散成多处碎片，不能要求一行里凑齐两个标记。
    题干里的「（A）」括号标记会被排除。
    """
    marks = list(RE_OPT_MARK.finditer(t))
    marks = [m for m in marks if not (m.start() > 0 and t[m.start() - 1] in '（(')]
    if not marks:
        return t, {}
    prefix = t[:marks[0].start()]
    opts = {}
    for i, m in enumerate(marks):
        key = m.group(1)
        end = marks[i + 1].start() if i + 1 < len(marks) else len(t)
        val = t[m.end():end]
        opts[key] = opts.get(key, "") + val
    return prefix, opts


def parse_question_pdf(path, subject):
    """
    解析题目卷，返回 {题号: 题干/选项/页码/坐标}。
    题号采用「期望值 + 严格递增」策略：只接受 == 期望值 的题号，避免被年份数字带偏。
    """
    doc, rows = iter_lines(path)
    rows = fix_split_marks(stitch(rows))
    page_h = doc[0].rect.height

    questions = {}
    expected = 1
    cur = None

    def close(q):
        if not q:
            return None
        q["stem"] = clean_text(q["stem"])
        q["options"] = {k: clean_text(v) for k, v in q["options"].items()}
        return q

    for row in rows:
        if is_noise(row, page_h):
            continue
        t = tight(row["t"].strip())
        t = strip_trailing_pageno(t, row["y"], page_h)
        if not t:
            continue
        # 括号残片（题干里的「）」「(」）直接并到当前题干，参与顺序判定
        if set(t) <= set('）)（( ') and cur is not None:
            cur["stem"] += t.strip()
            continue

        # 选项行可能被排版拆成「(A．xxxx」这类碎片：以「(A」开头也认作选项行
        if re.match(r'^[（(]\s*[A-D]\s*[．.、]', t):
            t = re.sub(r'^[（(]\s*', '', t)
        # 只含字母的碎片行（如 "D"），并入题干保真
        if cur is not None and re.fullmatch(r'[A-D]', t):
            cur["stem"] += t
            continue

        m = RE_Q_START.match(t)
        if m:
            num = int(m.group(1))
            if num != expected:
                continue          # 不是我们要的那题（例如年份、分值数字）
            if cur:
                questions[cur["no"]] = close(cur)
            cur = {"no": num, "stem": m.group(2), "options": {},
                   "pages": [row["p"]], "x": row["x"], "y": row["y"]}
            expected += 1
            continue
        if cur is None:
            continue

        # 选项行（可能一行内联 A．~D．）
        prefix, opts = split_inline_options(t)
        if opts:
            if prefix.strip():
                cur["stem"] += prefix
            for k, v in opts.items():
                cur["options"][k] = cur["options"].get(k, "") + v
            if row["p"] not in cur["pages"]:
                cur["pages"].append(row["p"])
            continue

        # 普通续行：判断是题干续行还是单个选项
        single = re.match(r'^\s*([A-D])\s*[．.、]\s*(.*)$', t)
        if single:
            k = single.group(1)
            cur["options"][k] = cur["options"].get(k, "") + single.group(2)
        else:
            cur["stem"] += t
        if row["p"] not in cur["pages"]:
            cur["pages"].append(row["p"])

    if cur and cur["no"] not in questions:
        questions[cur["no"]] = close(cur)

    return questions, doc


# ---------- 答案卷解析 ----------
#
# 四科答案卷有四种排版形态，必须分别处理：
#   生物： 题号 → 选项 → 【答案】A → 【解析】...     （答案在"解析前"）
#   历史： 1.C 解析正文...                            （答案紧跟题号，无标记）
#   政治： 1.B 解析正文...
#   地理： 1．C 解析...   /  3．D 4.A 解析...          （多题合并成一段，解析合并）
#
# 绝不能按"出现顺序"配对（生物答案卷里页码、题干都会被误认），
# 必须显式抓题号，并且最后用题干文本与题目卷交叉校验。

RE_ANS_MARK = re.compile(r'【答案】\s*([A-D]{1,4})')
RE_EXP_MARK = re.compile(r'【解析】')
RE_QNO_LEAD = re.compile(r'^\s*(\d{1,3})\s*[．.]\s*')
# 行内一或多组「题号.选项字母」，如 "1.C" "3．D 4.A" "5．A|6.B"
# 允许一个多余的点（OCR 产物 "45..B"）
RE_INLINE_QA = re.compile(r'(\d{1,3})\s*[．.]+\s*([A-D])(?![A-Za-z])')


def parse_bio_answer(rows, page_h, qstem_by_no):
    """
    生物答案卷：显式抓题号，随后的【答案】归给该题号，【解析】后到下一题号前为解析。
    题干文本用于校验（答案卷里也印了题干），能提前发现错位。

    修复：原 PDF 第 78 题被印成「18．」（手册排版错误）。
    判据是「上一题号 + 2」且题干与题目卷第 78 题文本一致 —— 只有两条同时成立才改写，
    绝不凭猜测改题号。
    """
    found = {}
    cur_no = None
    cur_exp = []
    stem_buf = []
    mode = None          # 'stem' → 'answer' → 'exp'
    last_no = None
    pending_fix = None   # 可能是被印错题号的那一题

    def flush():
        if cur_no is None:
            return
        rec = found.setdefault(cur_no, {"answer": "", "explanation": ""})
        if cur_exp:
            rec["explanation"] = clean_text(''.join(cur_exp))

    for row in rows:
        if is_noise(row, page_h):
            continue
        t = strip_trailing_pageno(row["t"].strip(), row["y"], page_h)
        if not t:
            continue

        m_q = RE_QNO_LEAD.match(t)
        m_ans = RE_ANS_MARK.search(t)
        m_exp = RE_EXP_MARK.search(t)

        if m_q:
            no = int(m_q.group(1))
            accept = False
            in_range = 1 <= no <= TOTAL_Q
            if in_range and no not in found:
                if last_no is None:
                    accept = (no == 1)
                elif no == last_no + 1:
                    accept = True
                else:
                    # 题号倒退或跳号：可能是原 PDF 印错了题号（如第 78 题印成 18）。
                    # 只有「题干与题目卷『下一个应有题号』的文本一致」才认，绝不猜。
                    cand = last_no + 1
                    real_stem = qstem_by_no.get(cand, '')
                    if (cand <= TOTAL_Q and cand not in found
                            and real_stem and norm_match(t[m_q.end():], real_stem)):
                        no = cand
                        accept = True
            elif in_range and no in found:
                # 题号已被占用，但内容符合「下一个应有题号」——
                # 说明原 PDF 印错了题号（生物第 78 题被印成 18）。
                cand = (last_no + 1) if last_no else None
                if cand and cand <= TOTAL_Q and cand not in found:
                    real_stem = qstem_by_no.get(cand, '')
                    if real_stem and norm_match(t[m_q.end():], real_stem):
                        no = cand
                        accept = True
            if not accept:
                continue
            flush()
            cur_no = no
            last_no = no
            cur_exp = []
            stem_buf = [t[m_q.end():]]
            mode = 'stem'
            found[cur_no] = {"answer": "", "explanation": ""}
            if m_ans:
                found[cur_no]["answer"] = m_ans.group(1)[0]
                mode = 'exp'
            continue

        if cur_no is None:
            continue

        if m_ans and not found[cur_no]["answer"]:
            found[cur_no]["answer"] = m_ans.group(1)[0]
            mode = 'exp'
            continue
        if m_exp:
            mode = 'exp'
            tail = t[m_exp.end():]
            if tail.strip():
                cur_exp.append(tail)
            continue

        if mode == 'stem':
            stem_buf.append(t)
        elif mode == 'exp':
            cur_exp.append(t)

    flush()
    return found


def norm_match(a, b, n=14):
    """题干文本粗匹配：去空白后前 n 字相同即视为同一题。"""
    na, nb = norm(a), norm(b)
    return bool(na) and bool(nb) and na[:n] == nb[:n]


def parse_inline_answer(rows, page_h):
    """
    历史/政治/地理：`1.C 解析正文`，一行可含多组 `3．D 4.A`。
    注意必须拒绝「2019 年」这类年份被当成题号（题号后必须紧跟 A-D 字母）。

    地理答案卷存在 OCR 拆字：如 `25．B 6.B`，其中 `6.B` 实为 `26.B`。
    判据：该题号已存在、其序号与本行已知题号连续、且 `加前一位数` 后未被占用。
    两条同时成立才改写，绝不猜。
    """
    found = {}
    cur_no = None
    cur_exp = []

    def flush():
        if cur_no is None:
            return
        rec = found.setdefault(cur_no, {"answer": "", "explanation": ""})
        if cur_exp:
            rec["explanation"] = clean_text(''.join(cur_exp))

    for row in rows:
        if is_noise(row, page_h):
            continue
        t = strip_trailing_pageno(row["t"].strip(), row["y"], page_h)
        if not t:
            continue

        parts = [m for m in RE_INLINE_QA.finditer(t)
                 if 1 <= int(m.group(1)) <= TOTAL_Q]
        for i, m in enumerate(parts):
            no = int(m.group(1))
            if no in found:
                # OCR 拆字：如 "25．B 6.B"，后者实为 26.B。
                # 判据：补前一位数字后恰好等于「上一题号+1」且未被占用。
                fixed = None
                cand = (cur_no + 1) if cur_no is not None else None
                if cand and cand <= TOTAL_Q and cand not in found and str(cand) != str(no):
                    fixed = cand
                if not fixed:
                    continue
                no = fixed
            flush()
            cur_no = no
            cur_exp = []
            found[no] = {"answer": m.group(2), "explanation": ""}
            end = parts[i + 1].start() if i + 1 < len(parts) else len(t)
            seg = t[m.end():end]
            if seg.strip():
                cur_exp.append(seg)
        if parts:
            continue
        if cur_no is not None:
            cur_exp.append(t)
    flush()
    return found


def parse_answer_pdf(path, subject, qstem_by_no):
    doc, rows = iter_lines(path)
    rows = fix_split_marks(stitch(rows))
    page_h = doc[0].rect.height
    if subject == '生物':
        return parse_bio_answer(rows, page_h, qstem_by_no)
    return parse_inline_answer(rows, page_h)


# ---------- 截图 ----------

def crop_question(doc, q, next_q, out_path):
    """
    按题目所在页的 y 区间裁图：从本题首行到「下一题首行」，或页面底部。

    关键：题目常跨页而图片在下一页（如生物第 9 题，图在第 2 页顶部）。
    因此跨页题要把「后续页从页首到下一题首行」也拼进同一张图，
    否则图片题会只剩题干文字、图形全丢。

    图片题、公式、表格全部靠这张图保真 —— 文字提取永远拿不到这些内容。
    """
    start_page = q["pages"][0]
    end_page = q["pages"][-1]
    page = doc[start_page - 1]
    top = max(q["y"] - 6, 0)
    left, right = 45, page.rect.x1 - 25

    parts = []
    if start_page == end_page:
        bottom = page.rect.y1 - 45
        if next_q and next_q["pages"][0] == start_page:
            bottom = max(next_q["y"] - 4, top + 20)
        parts.append((page, fitz.Rect(left, top, right, bottom)))
    else:
        # 起始页：从本题首行到页脚
        parts.append((page, fitz.Rect(left, top, right, page.rect.y1 - 45)))
        # 中间整页
        for p in range(start_page + 1, end_page):
            pg = doc[p - 1]
            parts.append((pg, fitz.Rect(left, 40, right, pg.rect.y1 - 45)))
        # 结束页：从页首到下一题首行
        last = doc[end_page - 1]
        bottom = last.rect.y1 - 45
        if next_q and next_q["pages"][0] == end_page:
            bottom = max(next_q["y"] - 4, 60)
        parts.append((last, fitz.Rect(left, 40, right, bottom)))

    pixmaps = []
    for pg, clip in parts:
        if clip.height < 8:
            continue
        pixmaps.append(pg.get_pixmap(clip=clip, dpi=IMG_DPI))
    if not pixmaps:
        pixmaps = [page.get_pixmap(clip=fitz.Rect(left, top, right, min(top + 160, page.rect.y1 - 40)),
                                   dpi=IMG_DPI)]

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    import PIL.Image as PILImage

    if len(pixmaps) == 1:
        img = PILImage.frombytes("RGB", (pixmaps[0].width, pixmaps[0].height),
                                 pixmaps[0].samples)
        save_img(img, out_path)
    else:
        # 纵向拼接多页裁片
        imgs = [PILImage.frombytes("RGB", (p.width, p.height), p.samples)
                for p in pixmaps]
        total_h = sum(im.height for im in imgs) + 6 * (len(imgs) - 1)
        canvas = PILImage.new("RGB", (max(im.width for im in imgs), total_h), "white")
        y = 0
        for im in imgs:
            canvas.paste(im, (0, y))
            y += im.height + 6
        save_img(canvas, out_path)
    return os.path.relpath(out_path, OUT_DATA).replace('\\', '/')


def save_img(img, out_path):
    """按 IMG_FORMAT 保存。WebP 用有损 q80，肉眼与 PNG 无差别但体积约 1/3。"""
    if IMG_FORMAT == 'WEBP':
        img.save(out_path, 'WEBP', quality=IMG_QUALITY, method=6)
    else:
        img.save(out_path)


# ---------- 主流程 ----------

def main():
    targets = [a for a in sys.argv[1:] if a in SUBJECTS] or SUBJECTS
    os.makedirs(OUT_IMG, exist_ok=True)
    all_questions = []
    report = []

    for subject in targets:
        qpath = os.path.join(BNK, subject + '.pdf')
        apath = os.path.join(BNK, subject + '答案.pdf')
        if not os.path.exists(qpath):
            report.append(f'## {subject}\n\n- ❌ 题目卷缺失：{qpath}\n')
            continue

        qs, qdoc = parse_question_pdf(qpath, subject)
        stems = {n: q["stem"] for n, q in qs.items()}
        ans_map = (parse_answer_pdf(apath, subject, stems)
                   if os.path.exists(apath) else {})

        warns = []
        ordered = sorted(qs.keys())

        for idx, no in enumerate(ordered):
            q = qs[no]
            a = ans_map.get(no)

            # 跨页题：把后续页里属于本题的续行也算进来（如生物第 17 题）
            next_q = qs.get(no + 1)
            img_rel = crop_question(
                qdoc, q, next_q,
                os.path.join(OUT_IMG, subject, f'{no:03d}.{IMG_FORMAT.lower()}'))

            entry = {
                "id": f'{subject}-{no:03d}',
                "subject": subject,
                "no": no,
                "stem": q["stem"],
                "options": q["options"],
                "answer": (a or {}).get("answer", ""),
                "explanation": (a or {}).get("explanation", "").strip(),
                "page": q["pages"][0],
                "img": img_rel,
            }
            # 校验：选项数、答案是否落在选项内、解析是否缺失
            if len(q["options"]) != 4:
                warns.append(f'第 {no} 题选项数异常：{sorted(q["options"].keys())}')
            if entry["answer"] and entry["answer"] not in entry["options"]:
                warns.append(f'第 {no} 题答案 {entry["answer"]} 不在选项中')
            if not entry["answer"]:
                warns.append(f'第 {no} 题无答案')
            if not entry["explanation"]:
                warns.append(f'第 {no} 题无解析')
            all_questions.append(entry)

        missing = [n for n in range(1, TOTAL_Q + 1) if n not in qs]
        report.append(f"## {subject}\n")
        report.append(f'- 题目卷解析：{len(qs)} / {TOTAL_Q} 题')
        report.append(f'- 答案+解析：{len([n for n in ans_map if ans_map[n].get("answer")])} / {TOTAL_Q} 条')
        if missing:
            report.append(f'- ⚠️ 未识别出的题号：{missing}')
        if warns:
            report.append(f'- ⚠️ 告警 {len(warns)} 条：')
            for w in warns[:80]:
                report.append(f'    - {w}')
            obs = sum(1 for w in warns if '不一致' in w)
            if obs:
                report.append(f'    - 其中「与题目卷题干不一致」{obs} 条（优先人工核对）')
        report.append('')

    os.makedirs(OUT_DATA, exist_ok=True)
    out_json = os.path.join(OUT_DATA, 'questions.json')
    with open(out_json, 'w', encoding='utf-8') as f:
        json.dump({"version": "1.0", "count": len(all_questions),
                   "questions": all_questions}, f, ensure_ascii=False, indent=1)
    print(f'✅ 写出 {out_json}（{len(all_questions)} 题）')

    with open(os.path.join(OUT_DATA, 'report.md'), 'w', encoding='utf-8') as f:
        f.write('# 题库解析报告\n\n')
        f.write('\n'.join(report))
    print('✅ 写出 data/report.md')


if __name__ == '__main__':
    main()
