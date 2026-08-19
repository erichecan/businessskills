# -*- coding: utf-8 -*-
import csv, json
D = '/sessions/keen-trusting-keller/mnt/素材库/'
TODAY = '2026-08-18'
data = json.load(open(D + '.run_0818r2/excel_data.json'))
stat = data['stat']

ROOTWORDS = ['职场表达','面试技巧','面试什么话该说','面试什么话不能说','实习谈薪','涨薪案例','推迟面试话术','会议礼仪发言']

def hit(k):
    v = stat[k]; t = v['xhs_tot'] + v['web_tot']; n = v['xhs_new'] + v['web_new']
    return n, t, (round(n / t * 100, 1) if t else 0.0)

AVG = {
 '职场表达': '约2.3万', '面试技巧': '约6千', '面试什么话该说': '约6千', '面试什么话不能说': '约6千',
 '实习谈薪': '约900', '涨薪案例': '约500', '推迟面试话术': '约350', '会议礼仪发言': '约2600',
}

# ===== 关键词池 =====
p = list(csv.reader(open(D + '关键词池.csv', encoding='utf-8-sig')))
ph = p[0]; prows = p[1:]
upg = []
retire = []
for r in prows:
    k = r[0].lstrip('﻿')
    if k in ROOTWORDS:
        n, t, h = hit(k)
        r[3] = str(int(r[3] or 0) + 1)
        r[4] = str(int(r[4] or 0) + n)
        r[5] = AVG.get(k, r[5])
        prev_note = r[6] or ''
        r[6] = f'08-18run2_{n}/{t}={h}%'
        r[8] = TODAY
        if r[1] == '候选' and h >= 40:
            r[1] = '活跃'; upg.append(k + f'(候选→活跃,{h}%)')
        if r[1] == '活跃' and h < 40:
            # count consecutive <40% occurrences is tracked informally in notes; here just flag single-round low
            pass

NEWP = ['催offer话术','涨薪成功案例分享','晋升调薪','开会发言礼仪','hr录用信号','面试结果询问话术','会议发言万能公式','面试话术秘招']
exist = {r[0].lstrip('﻿') for r in prows}
added_p = []
for w in NEWP:
    if w not in exist:
        prows.append([w, '候选', '08-18run2大家都在搜/相关搜索收割', '0', '0', '—', '—', TODAY, ''])
        added_p.append(w)

with open(D + '关键词池.csv', 'w', newline='', encoding='utf-8-sig') as f:
    w = csv.writer(f)
    w.writerow([c.lstrip('﻿') for c in ph])
    w.writerows(prows)

# ===== 词库 =====
kk = list(csv.reader(open(D + '词库.csv', encoding='utf-8-sig')))
kh = kk[0]; krows = kk[1:]
VERD = {
 '面试官说3天内给回复有没有希望': ('已验证', '高', '低'),
 '口头offer后怎么催进度': ('已验证', '高', '低'),
 'hr暗示你已经被录用了': ('放弃', '', ''),
 '微信怎么问面试结果话术': ('放弃', '', ''),
}
GAP = {
 '面试官说3天内给回复有没有希望': '答案空缺:前排出现首条按天数拆解的苗头(HR说三天给答复第几天没信就凉384赞),但仍是判读层(第几天没信=凉),没有给出第N天该主动做什么的动作模板;与已连续4轮验证的"面试完等通知期间怎么开口催进度"缺口同源(08-18run2)',
 '口头offer后怎么催进度': '重要进展:本词条件a命中完全同字面的原词笔记(昕哥说就业连发2条同标题),说明"口头offer后催进度"这个具体处境已形成独立搜索习惯,比泛化的"面试完催进度"更精准;但前排给出的多是"要不要催/催了会不会显得急"的心态建议,逐字可复制话术仍稀少(仅1-2条给出完整模板);建议作为"面试完等通知期间怎么催"这一跨轮空缺的突破口优先出稿(08-18run2)',
 'hr暗示你已经被录用了': '放弃理由:c不过,前排多篇>500(面试暗示你已经成功了1342/面试结果HR已经暗示过你3465/你马上要被录用了3183/面试博弈论10个强录取信号2970),"HR暗示信号"判读体子赛道已高度饱和,与此前"判读型内容止步于识别"结论一致(08-18run2)',
 '微信怎么问面试结果话术': '放弃理由:c不过,前排多篇>500(1208/1146/3506/5622),"要不要问/怎么问结果"方法层已饱和;但注意与已验证词"口头offer后怎么催进度"对比,说明泛化提问("怎么问结果")已饱和而精确到具体处境+具体节点(如口头offer后/3天节点后)的问法仍有空间,这是本轮最重要的方法论发现(08-18run2)',
}
for r in krows:
    k = r[0].lstrip('﻿')
    if k in VERD:
        s, i, c = VERD[k]
        r[7] = s
        if s == '已验证':
            r[3] = i; r[4] = c
        if k in GAP:
            r[11] = (r[11] + ' | ' if r[11] else '') + GAP[k]

NEWK = [
 ('hr说明天发offer是不是稳了', '面试后判读', '事件'),
 ('国企口头offer后怎么催进度', '谈薪', '事件'),
 ('怎样催offer比较礼貌', '谈薪', '事件'),
 ('面试完傻等通知怎么破', '面试后判读', '事件'),
 ('hr问你为什么放弃offer怎么回答', '谈薪', '事件'),
 ('面试延迟再约怎么说', '面试', '事件'),
 ('已经答应面试时间又要改期怎么说', '面试', '事件'),
 ('会议礼仪的稿子怎么写', '职场表达', '情境'),
 ('面试完一周没消息还要不要投别的', '面试后判读', '情境'),
 ('实习期间可以主动提涨薪吗', '谈薪', '事件'),
 ('涨薪多少合适', '谈薪', '情境'),
]
kexist = {r[0].lstrip('﻿') for r in krows}
added_k = []
for w, dom, typ in NEWK:
    if w not in kexist:
        krows.append([w, dom, typ, '', '', '', '', '候选', '', '', '', '08-18run2收割:①大家都在搜②相关搜索,已过相关性过滤'])
        added_k.append(w)

with open(D + '词库.csv', 'w', newline='', encoding='utf-8-sig') as f:
    w = csv.writer(f)
    w.writerow([c.lstrip('﻿') for c in kh])
    w.writerows(krows)

from collections import Counter
print('升级:', upg)
print('新增根词候选:', added_p)
print('新增长句候选:', len(added_k), added_k)
print('池类型分布:', Counter(r[1] for r in prows))
print('词库状态分布:', Counter(r[7] for r in krows))
