# -*- coding: utf-8 -*-
import csv
TODAY = "2026-08-13"
url = "https://www.xiaohongshu.com/search_result/68006d90000000001e0060e4?xsec_token=AB52wtiI_BSQNzpsbLbNL-qw0A80yz9m55XGQi3tgCmdo=&xsec_source="

rows = [
    (TODAY, url, "对我也有同样的体验 国内的面试 面试官没有给予我尊重", "面试中感觉不被尊重/得不到基本礼貌对待", "面试官不尊重人怎么办"),
    (TODAY, url, "要我国内时间下午面试🙃逆天", "跨时区/工作时间被要求配合不合理的面试安排", "面试时间安排不合理怎么办"),
]
with open("评论区原话.csv","a",encoding="utf-8-sig",newline='') as f:
    w = csv.writer(f)
    for row in rows:
        w.writerow(row)
print("appended:", len(rows))
