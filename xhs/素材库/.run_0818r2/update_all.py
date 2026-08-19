# -*- coding: utf-8 -*-
import csv, json, sys, os
D = '/sessions/keen-trusting-keller/mnt/素材库/'
sys.path.insert(0, D + '.run_0818r2')
from raw_data import XHS, XHS_NOLINK
TODAY = '2026-08-18'
RUN = 'run2'

# ---------- hrefs found for previously-nolink new items ----------
HREF = {
 '结构化面试什么不能说'.strip(): None,  # already had (unused)
}
NEWHREF = {
 '外企|北美职场|面试，这几句话不要说':'6a3bb70d0000000006035d12',
 '面试的时候，不该说的别乱说':'693f90c8000000001f00a8fb',
 '你有犯这些错误吗？':'6920378b000000001e02fa13',
 '实习谈薪全流程｜从准备到收尾一步到位':'6a7fda6100000000330338cf',
 'CA秋招｜NG谈薪常用英文收到Offer后':'6a681e2800000000140043f1',
 '🇨🇦转行数据分析五年💰3w→9w→13w→18w':'695c79bc0000000021030e83',
 '🇩🇪 从月薪3000到年薪102K，我用了六年':'69d8d2c8000000002b00d2c0',
 '看看小伙伴们今年工资都涨多少？':'69b096ab0000000023039a6e',
 '喜提涨工资':'69a8cc14000000002801d15b',
 '别踩雷‼️这么跟HR沟通面试改期才不扣印象分':'62abd7b1000000002103e455',
 '外企猎头真心话：想推迟面试，会有后果吗？':'644f282f000000000800c31d',
 '⚠️面试通知来的太急？':'68be9356000000001b022702',
 '离职、拒绝offer、推迟入职教科书级话术':'68a5512b000000001d0084c8',
 '回答面试邀请还有我这么蠢的人吗':'67d1863c000000000703646f',
 '🇨🇦总结经验，推迟面试':'66bd1ecb000000000d032387',
 '北美职场｜会议"聪明人"的必备金句':'67982a72000000001800db50',
 '登台礼仪':'674030e90000000008005063',
 '会上被cue到，开口即王炸！':'6a0482a400000000350396d2',
 '会议中怎么给来宾添茶水？超实用的实操分享':'6a841fa70000000028001c58',
 '告别上台尴尬｜1分钟教你拥有大气风格':'68732f990000000017037a6c',
 '北美职场开会不敢发言？其实你只差这3招！':'67e2173b000000000603dc94',
 '每次重要会议前都会做的7件事':'6876a530000000001202d2cd',
}
# items in NOLINK groups that we decided to drop (no href confirmed / low value on revisit)
DROPPED = ['半年赚150万的真实感受','老板给我涨了5K工资 我却更加焦虑不安',
 '39岁，涨薪150%，她的简历这样写。','🇩🇪跳槽一次，工资涨了40%，复盘4个关键动作',
 '窝囊但也许有用的北美小公司提涨薪公式']

# ---------- WEB items (real WebSearch results, URL-verified new) ----------
WEB = {
 '推迟面试话术': [
  ('求助，因痛经想推迟面试，该如何和hr说？','https://www.zhihu.com/question/265655281','知乎'),
  ('主动推迟面试时间是面试的大忌_推迟面试时间的得体说辞','https://blog.csdn.net/Hello_Chillax/article/details/104722120','CSDN'),
 ],
 '面试改期': [
  ('面试后没回复，怎么礼貌询问HR结果？','https://zhuanlan.zhihu.com/p/510188193','知乎专栏'),
 ],
 '实习谈薪': [
  ('面试谈薪技巧|不要傻傻被坑啦','https://www.nowcoder.com/feed/main/detail/aa0899aeaba64ea09f9b5eb932a67d67','牛客网'),
 ],
}

# ---------- load memory ----------
memrows = list(csv.reader(open(D + '职场面试_记忆库.csv', encoding='utf-8-sig')))
hdr = memrows[0]
existing_titles = set(r[0].strip() for r in memrows[1:] if r)
existing_urls = set(r[1].strip() for r in memrows[1:] if len(r) > 1)

rows_out = []  # 关键词,来源,新增/已收,标题,作者/站点,热度,链接
newmem = []
stat = {}

def xhs_url(nid):
    return 'https://www.xiaohongshu.com/explore/' + nid if nid else ''

for kw, items in XHS.items():
    tot = 0; new = 0
    for t, au, dt, hot, nid in items:
        tot += 1
        url = xhs_url(nid)
        isnew = t.strip() not in existing_titles
        flag = '今日新增' if isnew else '此前已收'
        if isnew:
            new += 1
            newmem.append([t, url, '小红书', kw, TODAY, hot])
            existing_titles.add(t.strip()); existing_urls.add(url)
        rows_out.append([kw, '小红书', flag, t, au, hot, url])
    stat[kw] = {'xhs_tot': tot, 'xhs_new': new, 'web_tot': 0, 'web_new': 0}

for kw, items in XHS_NOLINK.items():
    d = stat.setdefault(kw, {'xhs_tot': 0, 'xhs_new': 0, 'web_tot': 0, 'web_new': 0})
    for t, au, dt, hot in items:
        if t in DROPPED:
            continue
        d['xhs_tot'] += 1
        isnew = t.strip() not in existing_titles
        nid = NEWHREF.get(t)
        url = xhs_url(nid) if nid else ''
        flag = '今日新增' if isnew else '此前已收'
        if isnew:
            d['xhs_new'] += 1
            newmem.append([t, url, '小红书', kw, TODAY, hot])
            existing_titles.add(t.strip())
            if url: existing_urls.add(url)
        rows_out.append([kw, '小红书', flag, t, au, hot, url])

for kw, items in WEB.items():
    d = stat.setdefault(kw, {'xhs_tot': 0, 'xhs_new': 0, 'web_tot': 0, 'web_new': 0})
    for t, u, site in items:
        d['web_tot'] += 1
        isnew = (u not in existing_urls) and (t.strip() not in existing_titles)
        flag = '今日新增' if isnew else '此前已收'
        if isnew:
            d['web_new'] += 1
            newmem.append([t, u, '网页搜索', kw, TODAY, '—'])
            existing_urls.add(u); existing_titles.add(t.strip())
        rows_out.append([kw, '网页搜索', flag, t, site, '—', u])

with open(D + '职场面试_记忆库.csv', 'a', newline='', encoding='utf-8') as f:
    csv.writer(f).writerows(newmem)

print('新增记忆库条数:', len(newmem))
total_tot = 0; total_new = 0
for k, v in stat.items():
    t = v['xhs_tot'] + v['web_tot']; n = v['xhs_new'] + v['web_new']
    total_tot += t; total_new += n
    pct = round(n / t * 100, 1) if t else 0.0
    print(f"{k}: {n}/{t} = {pct}%  (XHS {v['xhs_new']}/{v['xhs_tot']}, web {v['web_new']}/{v['web_tot']})")
print('本轮总抓取:', total_tot, ' 本轮新增:', total_new)
print('记忆库累计:', len(memrows) - 1 + len(newmem))

json.dump({'rows': rows_out, 'stat': stat}, open(D + '.run_0818r2/excel_data.json', 'w'), ensure_ascii=False)
