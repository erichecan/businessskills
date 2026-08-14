# -*- coding: utf-8 -*-
import csv

TODAY = "2026-08-13"
new_candidates = [
    ("无领导小组材料分析题","候选","小红书搜索建议(无领导小组讨论的题目有哪些词库验证轮衍生)","0","0","—","—",TODAY,"—"),
    ("hr聊天沟通时的话术","候选","小红书猜你想搜(面试被hr阴阳怪气笔记详情页衍生)","0","0","—","—",TODAY,"—"),
    ("领导气场培养","候选","小红书搜索建议(控场能力和领导能力怎么培养词库验证轮衍生)","0","0","—","—",TODAY,"—"),
]

with open("关键词池.csv","a",encoding="utf-8-sig",newline='') as f:
    w = csv.writer(f)
    for row in new_candidates:
        w.writerow(row)
print("appended:", len(new_candidates))
