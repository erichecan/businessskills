# -*- coding: utf-8 -*-
import csv, json
D='/sessions/adoring-stoic-lamport/mnt/素材库/'
TODAY='2026-08-18'
stat=json.load(open(D+'.run_0818/excel_data.json'))['stat']

def hit(k):
    v=stat[k]; t=v['xhs_tot']+v['web_tot']; n=v['xhs_new']+v['web_new']
    return n,t,round(n/t*100,1)

# ===== 关键词池 =====
p=list(csv.reader(open(D+'关键词池.csv')))
ph=p[0]; prows=p[1:]
AVG={'职场表达':'约2.5万','面试技巧':'约5千','面试什么话该说':'约6千','面试什么话不能说':'约6千',
     '职场漂亮话':'约1.3万','自我介绍万能公式':'约6千','终面潜台词':'约3.4万','面试改期':'约72'}
upg=[]
for r in prows:
    k=r[0].lstrip('﻿')
    if k in stat:
        n,t,h=hit(k)
        r[3]=str(int(r[3] or 0)+1)
        r[4]=str(int(r[4] or 0)+n)
        r[5]=AVG.get(k,r[5])
        r[6]=f'08-18run1_{n}/{t}={h}%'
        r[8]=TODAY
        if r[1]=='候选' and h>=40:
            r[1]='活跃'; upg.append(k+f'(候选→活跃,{h}%)')
# 新候选短词
NEWP=['压力型面试问题','二面压力面','终面ceo面','董事长终面','会议礼仪发言','推迟面试话术','高情商回话','面试婉拒']
exist={r[0].lstrip('﻿') for r in prows}
added_p=[]
for w in NEWP:
    if w not in exist:
        prows.append([w,'候选','08-18run1大家都在搜/知乎问题标题收割','0','0','—','—',TODAY,''])
        added_p.append(w)
with open(D+'关键词池.csv','w',newline='',encoding='utf-8-sig') as f:
    w=csv.writer(f); w.writerow([c.lstrip('﻿') for c in ph]); w.writerows(prows)

# ===== 词库 =====
kk=list(csv.reader(open(D+'词库.csv')))
kh=kk[0]; krows=kk[1:]
VERD={
 '收到面试邀请但还有事怎么回复推迟面试':('已验证','高','低'),
 '开会被点名发言要站起来吗':('已验证','高','低'),
 '面试完等通知3天了还没回复怎么委婉的问':('已验证','高','中'),
 '终面老板问你对公司的看法':('放弃','',''),
 '压力面试被说要淘汰还能通过吗':('放弃','',''),
}
GAP={
 '收到面试邀请但还有事怎么回复推迟面试':'答案空缺:中文侧仅1条给话术(别踩雷这么跟HR沟通面试改期344赞),其余为英文邮件模板或会不会有影响的判读;缺已答应时间后二次改期的话术(08-18run1)',
 '开会被点名发言要站起来吗':'答案空缺:XHS侧27条无一条讲站坐/看谁/拿不拿本子等现场动作,而web侧职场礼仪源明确给出被点到名才站起来的规范,平台间供给差可直接搬运(08-18run1)',
 '面试完等通知3天了还没回复怎么委婉的问':'答案空缺(第4轮交叉验证):供给仍全在该不该问/别傻等/3招催出来的方法层,零条给可复制原话;且3天这一具体天数在大家都在搜4条变体中全为模糊表述,按承诺天数节点开口=空白(08-18run1)',
}
for r in krows:
    k=r[0].lstrip('﻿')
    if k in VERD:
        s,i,c=VERD[k]
        r[7]=s
        if s=='已验证': r[3]=i; r[4]=c
        if k in GAP: r[11]=(r[11]+' | ' if r[11] else '')+GAP[k]
NEWK=[
 ('怎么委婉的更改面试时间','面试','事件'),('推迟面试时间话术','面试','情境'),
 ('怎么跟hr说推迟面试','面试','事件'),('临时推迟面试怎么说','面试','事件'),
 ('领导来了要站起来说话','职场表达','事件'),('大领导来需要站起来吗','职场表达','事件'),
 ('每次开会轮到我发言','职场表达','症状'),('终面ceo面试一般问什么','面试','情境'),
 ('小公司老板终面一般问什么','面试','情境'),('终面问大老板的问题','面试','事件'),
 ('董事长终面一般问什么','面试','情境'),('面试完没有回复怎么询问','面试后判读','事件'),
 ('一直不回怎么询问面试结果','面试后判读','事件'),('面试完后怎么询问结果','面试后判读','事件'),
 ('压力面试的意义','面试','情境'),('不想去面试了该怎么跟hr说','面试','事件'),
 ('跟hr说改时间面试可以吗','面试','事件'),('推迟面试怎么跟人力沟通','面试','事件'),
 ('面试过了一周如何向hr询问结果','面试后判读','事件'),('有哪些一开口就很哇塞的高情商','职场表达','症状'),
]
kexist={r[0].lstrip('﻿') for r in krows}
added_k=[]
for w,dom,typ in NEWK:
    if w not in kexist:
        krows.append([w,dom,typ,'','','','','候选','','','','08-18run1收割:①大家都在搜②知乎问题标题,已过相关性过滤'])
        added_k.append(w)
with open(D+'词库.csv','w',newline='',encoding='utf-8-sig') as f:
    w=csv.writer(f); w.writerow([c.lstrip('﻿') for c in kh]); w.writerows(krows)

from collections import Counter
print('升级:',upg)
print('新增根词候选:',added_p)
print('新增长句候选:',len(added_k),added_k)
print('池:',Counter(r[1] for r in prows))
print('词库:',Counter(r[7] for r in krows))
