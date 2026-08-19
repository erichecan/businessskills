# -*- coding: utf-8 -*-
import csv, io, re
BASE='/sessions/bold-beautiful-johnson/mnt/素材库/'
D='2026-08-15'
RUN='08-15run5'

# ===== 关键词池更新 =====
p=BASE+'关键词池.csv'
rows=list(csv.DictReader(io.open(p,encoding='utf-8-sig')))
FN=['关键词','类型','来源','运行次数','累计新增条数','平均热度','命中率','首次发现','最近运行']
# 本轮跑过的根词: (词, 抓取, 原始新增, 过滤后新增, 平均热度, 升降级)
RES={
 '职场表达':      (18,1,0,'—','维持种子'),
 '面试技巧':      (20,0,0,'—','维持种子'),
 '面试什么话该说':(10,0,0,'—','维持种子'),
 '面试什么话不能说':(10,0,0,'—','维持种子'),
 '三明治拒绝法(原职场三明治拒绝法)':(12,10,8,'约1568','维持活跃'),
 '麦肯锡表达(原麦肯锡逻辑表达术)':(12,7,3,'约1363','维持活跃'),
 '体面拒绝话术':  (12,7,4,'约1564','维持候选'),
 '高情商接话公式':(12,6,6,'约5153','候选升活跃'),
}
def num(v):
    try: return int(str(v).strip() or 0)
    except: return 0
hit_report=[]
for r in rows:
    k=r['关键词']
    if k in RES:
        grab,raw,filt,heat,act=RES[k]
        r['运行次数']=str(num(r['运行次数'])+1)
        r['累计新增条数']=str(num(r['累计新增条数'])+filt)
        rate=round(filt*100.0/grab,1)
        r['命中率']=f'{RUN}:{rate}%({filt}新/{grab}抓;原始{raw}条经相关性过滤后{filt}条)'
        if heat!='—': r['平均热度']=heat
        r['最近运行']=D
        if act=='候选升活跃': r['类型']='活跃'
        hit_report.append((k,grab,raw,filt,rate,heat,act))
with io.open(p,'w',encoding='utf-8-sig',newline='') as f:
    w=csv.DictWriter(f,fieldnames=FN); w.writeheader()
    for r in rows: w.writerow({k:r.get(k,'') for k in FN})

# 新收割根词 -> 候选
NEWPOOL=[('面试大脑空白怎么学','小红书下拉补全(面试大脑空白,2026-08-15run5)'),
 ('面试大脑空白没词怎么办','小红书下拉补全(面试大脑空白,2026-08-15run5)'),
 ('面试临场发挥','小红书搜索筛选标签(面试被问到细节答不上来怎么办,2026-08-15run5)'),
 ('面试失败案例','小红书搜索筛选标签(面试被问到细节答不上来怎么办,2026-08-15run5)'),
 ('面试问题打不出来怎么办','小红书大家都在搜(面试被问到细节答不上来怎么办,2026-08-15run5)'),
 ('职场高级表达','小红书大家都在搜(职场表达,2026-08-15run5)'),
 ('万能场面话','高情商接话公式轮 前排标题高频原句(2026-08-15run5)'),
]
existk={r['关键词'] for r in rows}
with io.open(p,'a',encoding='utf-8',newline='') as f:
    w=csv.DictWriter(f,fieldnames=FN)
    c=0
    for k,src in NEWPOOL:
        if k in existk: continue
        w.writerow({'关键词':k,'类型':'候选','来源':src,'运行次数':'0','累计新增条数':'0','平均热度':'','命中率':'','首次发现':D,'最近运行':''})
        c+=1
print('关键词池 新增候选',c)
for h in hit_report: print('  ',h)

# ===== 词库更新 =====
p2=BASE+'词库.csv'
rows2=list(csv.DictReader(io.open(p2,encoding='utf-8-sig')))
FN2=list(rows2[0].keys()) if rows2 else []
VERDICT={
 '面试被问到细节答不上来怎么办':('已验证','情境','高','中','a:下拉补全出现近似词"面试被问到细节怎么办"通过;b:结果20+篇属较多未饱和;c:前排10篇6篇<500(20/137/163/251/16/499),仅4篇超500;答案空缺=前排全在讲心态与承认盲区,无一篇给出"承认+关联+把主动权拉回"的逐句救场话术模板,同类最低赞样本(老K项目被追问细节20赞)证明供给薄弱'),
 '面试突发问题大脑空白话术':('放弃','症状','中','高','a通过(下拉补全10条极丰富);b较多;c不通过=前排10篇6篇超500(1371/1266/695/2468/2399/2.4万);现有答案不弱,不建议正面挤入,但"突发问题当场接话"细分角度仍空缺'),
 '新公司要求两周内入职老东家不放人怎么办':('放弃','事件','高','—','a不通过:"老东家不放人"无任何下拉补全,非真实搜索需求'),
 '原公司卡着社保不转出怎么办':('放弃','事件','高','—','a不通过:"原公司卡着社保"补全全部为"公司社保减员原因"类不同义查询,原词非真实需求'),
 '公司以没请到人交接拖着不让走怎么办':('放弃','事件','高','—','a不通过:"交接拖着不让走"无任何下拉补全'),
}
def col(r,*names):
    for n in names:
        if n in r: return n
    return None
n_ver=0
for r in rows2:
    k=r.get('关键词','').strip()
    if k in VERDICT:
        st,it,inten,comp,note=VERDICT[k]
        r['状态']=st
        if '场景类型' in r: r['场景类型']=it
        if '意图强度' in r: r['意图强度']=inten
        if '竞争密度' in r: r['竞争密度']=comp
        if '备注' in r: r['备注']=(r.get('备注','') or '')+f'[{RUN}]{note}'
        n_ver+=1
with io.open(p2,'w',encoding='utf-8-sig',newline='') as f:
    w=csv.DictWriter(f,fieldnames=FN2); w.writeheader()
    for r in rows2: w.writerow({k:r.get(k,'') for k in FN2})
print('词库 已更新状态行数',n_ver)

# 新收割长句 -> 词库候选
NEWKU=['面试大脑空白时说的话','面试大脑空白可以编吗','面试时大脑一片空白怎么办','面试大脑空白不会说',
 '面试有好几道题都不会怎么办','面试只知道部分答案该怎么措辞','面试被问不会的问题可以说给我五分钟查一下吗',
 '面试答不上来时先说一句什么来拖时间','面试被追问细节怎么把主动权拉回自己手里','面试遇到完全不会的问题怎么办']
existk2={r.get('关键词','').strip() for r in rows2}
with io.open(p2,'a',encoding='utf-8',newline='') as f:
    w=csv.DictWriter(f,fieldnames=FN2)
    c=0
    for k in NEWKU:
        if k in existk2: continue
        row={n:'' for n in FN2}
        row['关键词']=k; row['状态']='候选'
        if '场景类型' in row: row['场景类型']='待归类'
        if '备注' in row: row['备注']=f'[{RUN}]收割自面试大脑空白下拉补全/Vivid聊面试2189赞笔记评论区原话'
        w.writerow(row); c+=1
print('词库 新增候选',c)
