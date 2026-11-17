# -*- coding: utf-8 -*-
"""
verify_bank.py —— 题库交叉校验（不编造，只做一致性检查）

检查项
------
1. 每科 100 题、题号连续
2. 每题都有答案（A-D）且答案落在选项内
3. 选项齐全（4 个，图片选项题除外）
4. 每题都有截图文件
5. 解析里若提到「故选X / 答案X / X正确」等，与答案字段是否冲突
6. 重复题检测（题干哈希）

用法：python verify_bank.py
"""
import json
import os
import re
import sys
from collections import Counter, defaultdict

sys.stdout.reconfigure(encoding='utf-8')

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, 'data')


def norm(s):
    return re.sub(r'[^\u4e00-\u9fffA-Za-z0-9]', '', s or '')


def main():
    path = os.path.join(DATA, 'questions.json')
    with open(path, encoding='utf-8') as f:
        bank = json.load(f)
    qs = bank['questions']

    problems = []
    stats = Counter()

    by_subject = defaultdict(list)
    for q in qs:
        by_subject[q['subject']].append(q)

    for subject, items in by_subject.items():
        nos = sorted(q['no'] for q in items)
        if len(items) != 100:
            problems.append(f'{subject}: 只有 {len(items)} 题')
        if nos != list(range(1, 101)):
            miss = [n for n in range(1, 101) if n not in nos]
            problems.append(f'{subject}: 题号不连续，缺 {miss}')

    for q in qs:
        tag = f"{q['subject']}{q['no']:03d}"

        if not q.get('answer'):
            problems.append(f'{tag}: 无答案')
            stats['无答案'] += 1

        opts = q.get('options') or {}
        if len(opts) != 4:
            # 图片选项题（如地理 64）选项本身就是图，合法
            if q.get('img'):
                stats['图片选项题'] += 1
            else:
                problems.append(f'{tag}: 选项数 {len(opts)}')
                stats['选项不全'] += 1

        if q.get('answer') and opts and q['answer'] not in opts:
            problems.append(f'{tag}: 答案 {q["answer"]} 不在选项中')
            stats['答案越界'] += 1

        img = q.get('img')
        if not img or not os.path.exists(os.path.join(DATA, img)):
            problems.append(f'{tag}: 截图缺失 {img}')
            stats['截图缺失'] += 1

        if not q.get('explanation'):
            stats['无解析'] += 1

        # 解析自证：只有当解析「只给出单一结论」时才比对。
        # 地理卷解析常把连续几题的解析合并成一段（"第19题…故选C。第20题…故D正确。"），
        # 这种情况会出现多个结论，不能据此判定冲突。
        exp = q.get('explanation') or ''
        verdicts = set()
        for m in re.finditer(r'(?:故选|答案选|答案为|应选)\s*([A-D])\b', exp):
            verdicts.add(m.group(1))
        for m in re.finditer(r'故\s*([A-D])\s*(?:项)?\s*正确', exp):
            verdicts.add(m.group(1))
        if len(verdicts) == 1 and q.get('answer') and verdicts != {q['answer']}:
            stats['解析与答案冲突'] += 1
            problems.append(
                f'{tag}: 解析唯一结论是 {list(verdicts)[0]}，但答案是 {q["answer"]}')

    # 重复题
    seen = defaultdict(list)
    for q in qs:
        h = norm(q.get('stem'))[:60]
        if h:
            seen[h].append(f"{q['subject']}{q['no']}")
    dup = {k: v for k, v in seen.items() if len(v) > 1}
    if dup:
        stats['重复题干'] = len(dup)

    print('=' * 50)
    print(f"题库总数：{len(qs)}")
    for s in ['生物', '历史', '政治', '地理']:
        print(f"  {s}: {len(by_subject.get(s, []))} 题")
    print('-' * 50)
    for k, v in stats.most_common():
        print(f'  {k}: {v}')
    print('-' * 50)
    if problems:
        print(f'❌ 发现 {len(problems)} 个问题：')
        for p in problems[:80]:
            print('   ', p)
        if len(problems) > 80:
            print(f'    ... 还有 {len(problems) - 80} 条')
    else:
        print('✅ 全部校验通过')


if __name__ == '__main__':
    main()
