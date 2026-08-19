# -*- coding: utf-8 -*-
import csv, json, re, os
D='/sessions/adoring-stoic-lamport/mnt/素材库/'
TODAY='2026-08-18'
dedup=json.load(open(D+'.run_0818/xhs_dedup.json'))

# ---------- WEB items ----------
WEB={
'面试改期':[
 ('不想去面试了，该怎么跟 hr 说呢？','https://www.zhihu.com/question/7961885508','—'),
 ('跟HR说改时间面试可以吗？','https://www.zhihu.com/question/307234249/answer/3334024340','—'),
 ('推迟面试怎么跟人力沟通？正确表达推迟面试的方法有哪些？','https://blog.ihr360.com/p/20530/','—'),
 ('怎么跟hr沟通推迟面试时间','https://m.jianli.com/article/dglmbo.html','—'),
 ('一个面试小技巧（二）','https://zhuanlan.zhihu.com/p/566465996','—'),
],
'终面潜台词':[
 ('面试官的"潜台词"，你听得懂吗?','https://zhuanlan.zhihu.com/p/161492362','—'),
 ('连续3次一面挂，我终于听懂面试官的潜台词','https://www.nowcoder.com/discuss/353154604214984704','—'),
 ('面试后HR的10大"潜台词"解析','https://zhuanlan.zhihu.com/p/1900636279772783058','—'),
 ('解惑丨这11句面试"潜台词"，没一条是准确的','https://zhuanlan.zhihu.com/p/405375433','—'),
 ('面试官的潜台词你知道哪些？','https://www.nowcoder.com/discuss/779303786195054592','—'),
 ('面试官的6句"话里有话"','https://36kr.com/p/681497587810434','—'),
 ('一定要听懂的面试官的"潜台词"！','https://zhuanlan.zhihu.com/p/12373042673','—'),
 ('面试官每句话都暗含"套路"？真正靠谱求职靠什么？','https://aeo.uibe.edu.cn/front/showContent.jspa?channelId=641&contentId=4343','—'),
],
'职场漂亮话':[
 ('万能高情商说话方式：42个全场景话术','https://www.sohu.com/a/1015336307_121884823','—'),
 ('高情商聊天话术','https://lusongsong.com/yulu/t/14521.html','—'),
 ('职场高情商沟通话术，和领导同事相处不踩雷','https://k.sina.cn/article_7857201856_1d45362c001908c2qw.html','—'),
 ('安抚情绪|化解抱怨|回应夸奖 - 职场12句高情商话术','https://www.sina.cn/news/detail/5306819973219331.html','—'),
 ('职场不会沟通？学会这10个高情商话术','https://www.sohu.com/a/1042075501_122068545','—'),
 ('高情商回话100句｜不伤人、不委屈、不得罪，日常聊天万能公式','https://www.toutiao.com/article/7609887791661236736/','—'),
 ('有哪些一开口就很"哇塞"的高情商？','https://www.zhihu.com/question/1954673202560230442','—'),
 ('2026马年高情商祝福语 职场 高情商 社交 人情世故','https://m.sohu.com/a/1004184678_100114195','—'),
],
'职场表达':[
 ('开会时，领导突然点名让你讲几句，但你却没有提前做好准备的时候，该怎么办？','https://www.zhihu.com/question/454031031','—'),
 ('盘点职场人士必知的会议礼仪-职场礼仪','https://www.yjbys.com/qiuzhiliyi/zcly/2704998.html','—'),
 ('职场社交礼仪的四大基本原则，你知道多少？','https://zhuanlan.zhihu.com/p/101904467','—'),
 ('《职场礼仪与商务接待培训：塑造企业专业形象的员工行为规范指南》','https://www.shangshanjingji.com/blog-detail/NjmeJPdB','—'),
],
'面试技巧':[
 ('面试过了一周，如何向hr 询问结果？','https://www.zhihu.com/question/477550829','—'),
],
}

# ---------- load memory ----------
memrows=list(csv.reader(open(D+'职场面试_记忆库.csv')))
hdr=memrows[0]
existing_urls=set(); existing_titles=set()
for r in memrows[1:]:
    if len(r)>1:
        existing_urls.add(r[1].strip()); existing_titles.add(r[0].strip())

rows_out=[]   # for excel: 关键词,来源,新增/已收,标题,作者/站点,热度,链接
newmem=[]
stat={}
for kw, items in dedup.items():
    tot=0;new=0
    for nid,t,au,lk,flag in items:
        tot+=1
        url='https://www.xiaohongshu.com/explore/'+nid if nid else ''
        isnew = flag=='今日新增'
        if isnew:
            new+=1
            newmem.append([t,url,'小红书',kw,TODAY,lk])
            existing_urls.add(url); existing_titles.add(t)
        rows_out.append([kw,'小红书',flag,t,au,lk,url])
    stat[kw]={'xhs_tot':tot,'xhs_new':new}

for kw, items in WEB.items():
    stat.setdefault(kw,{'xhs_tot':0,'xhs_new':0})
    wt=0;wn=0
    for t,u,lk in items:
        wt+=1
        isnew = u not in existing_urls and t not in existing_titles
        if isnew:
            wn+=1
            newmem.append([t,u,'网页搜索',kw,TODAY,lk])
            existing_urls.add(u); existing_titles.add(t)
        rows_out.append([kw,'网页搜索','今日新增' if isnew else '此前已收',t,u.split('/')[2],lk,u])
    stat[kw]['web_tot']=wt; stat[kw]['web_new']=wn
for k in stat: stat[k].setdefault('web_tot',0); stat[k].setdefault('web_new',0)

with open(D+'职场面试_记忆库.csv','a',newline='',encoding='utf-8') as f:
    csv.writer(f).writerows(newmem)

json.dump({'rows':rows_out,'stat':stat},open(D+'.run_0818/excel_data.json','w'),ensure_ascii=False)
print('新增记忆库条数:',len(newmem))
for k,v in stat.items():
    tot=v['xhs_tot']+v['web_tot']; new=v['xhs_new']+v['web_new']
    print(f"{k}: {new}/{tot} = {round(new/tot*100,1)}%  (XHS {v['xhs_new']}/{v['xhs_tot']}, web {v['web_new']}/{v['web_tot']})")
print('记忆库累计:', len(memrows)-1+len(newmem))
