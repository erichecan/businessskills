# -*- coding: utf-8 -*-
import csv

TODAY = "2026-08-13"

memory_rows = [
("压力面试，是你甩开对手的好机会（第三期）","69a11684000000001a026624","ABZ-ut7Qx7gEc2431nczyoanX_l0BWznSZdUP5dzRT0RI=","面试反杀面试官","4.4万"),
("用 AI 面试作弊，还拿到了亚马逊的offer","67c81863000000002a00fe39","ABVRlWIBdBAO3f4eV6TNqA65as5H8iY3nP1b2kbzR3CX8=","面试反杀面试官","8307"),
("怎么让面试官对你增加好感","69af6c3d000000001503b0dc","AB_Ikwr_oU4Dcge5T9H1i0wSbaycebsNTwTcEHRDeJhXQ=","面试反杀面试官","7215"),
("一个小技巧 面试官也想帮你拿offer","6a46e2b4000000000e021800","ABpOtIMLDH4jJTZZrsOXw4pSxtNZjHgsPQD6_NLOi8Wi0=","面试反杀面试官","533"),
("假如面试官说真话","69ab5d38000000001d013859","ABnKti2O_OHU3FClrBkxVLeyE1h7t1y_bprhK7d7uPPUU=","面试反杀面试官","5893"),
("拒绝题海战术 把面试官框在你的思路里","695346160000000021028055","ABNrycHnQDFJUd8jY2-YmsIk0Gwh5a3pZVwUGKhQwirjQ=","面试反杀面试官","2741"),
("把面试官绕在你的思路里","69b20c06000000001503b876","ABdb0EnkF1RuquHphATtQx0wQUWvh7i0SIbt4OD8VJ8T8=","面试反杀面试官","3702"),
("让面试官知道咱们中国人软硬实力兼备","6967e0c400000000220388d8","ABbDLQPS_2V4r_lvZ5myGTyr7ImdlUIHY50M_0J6RsP8k=","面试反杀面试官","2617"),
("把压力给到面试官！","6909ed5c0000000003038a80","ABHD4wwLZpEZgDk0Wt_qHNu6VOItXnMY3rfF807ZkulKY=","面试反杀面试官","2365"),
("高压面试如何用“停顿‘反杀面试官？","69fb95ff000000000f03ac00","ABySzai4nLI67tf-ONK9YhYwvxmTDBNbwLzuZpwtLzUDc=","面试反杀面试官","23"),

("做了个训练讲话的网页-2.0版本-第2期","6a6ddae6000000002c005de5","ABk31yDVGRaeJy7muaLFSpuKHReolTCeosjRf0BZbWHTA=","职场表达","2690"),
("麦肯锡顾问：废话太多怎么办？（连载01）","69b7e24200000000230203fd","AB-_QWW-qMGrRIhO5HhjzFqYwl0oXkMGUbOA2qRb2NXHg=","职场表达","689"),
("精选外刊丨人一定要大量频繁的说话","6a4bb169000000001603c6ad","AB78zpW53s2LixtvzaoSp1XjMYU_ml5MB80jklPiPD4sQ=","职场表达","2290"),
("那些外国同事每天都用的地道表达（十八）","6a460e3d00000000170080f5","ABpOtIMLDH4jJTZZrsOXw4pd9rk9jmtwZgZy_JtsMy19c=","职场表达","919"),
("白女同事亲授如何在会议里“自然抢到话语权”","694938ee000000001e02f9a8","ABBGSvQuwpmbnGZmzU7E7tX77QnTFkDt9fqnk-1YZ1T4I=","职场表达","381"),
("表达能力，决定了你的职场上限","69db808200000000230202d1","ABU_FwWTZVdUj5at-q8TnB81_C1sWF7DlMBFywCwD9IZM=","职场表达","7.1万"),

("1个面试不卡壳的万能话术","6a6bc91a0000000008009c00","ABd2kIn4bXJvoibTlK7X9tR06ZjVOZsnndGVnyPAdLeHA=","面试技巧","659"),

("面试时千万不要说这句话","69ab30d80000000026033031","ABnKti2O_OHU3FClrBkxVLe2Pb7IO-GmXnOmdaBMcrh24=","面试什么话不能说","193"),
("面试时最让HR讨厌的3种自我介绍","6a031219000000003701faf1","ABZeUe5sJ0ci7e_0MDNUPghWNFXp2uIY2_7opemJhVQMQ=","面试什么话不能说","133"),
("面试像聊天 反而拿offer","67188ff80000000021002cbf","ABxshC36zcitx4_nQnge_7lAXovNcidBmox--OuA-16j4=","面试什么话不能说","1.2万"),

("面试感到不被尊重，那是贵人面试官叫你快跑","680a0d14000000000b01cb71","ABOarNiFIToxm-YZiHQ7vT5tQyADid8hBONduqOyXhoBQ=","面试被hr阴阳怪气","811"),
("我开始在面试中怼面试官了","69d5521c0000000022003438","ABV2YhTVTfSTK65SR4NFFjKI4e1eZoUlefh_J_wwe3wV4=","面试被hr阴阳怪气","255"),
("面试被问不稳定，怎么回答","69368a43000000000d00c8d2","ABKj4CHkEKquroeemQLuoiAwcfwIvPSwiCFiIgdpSIsM4=","面试被hr阴阳怪气","—"),
("遇到无礼的面试官请果断礼貌的回怼过去！","69dcd3f40000000023015ca6","ABlXCt1FSztGRKYJ7UtCpnYBxUr_PxaTkaTT_AUjQsYo4=","面试被hr阴阳怪气","—"),
("为什么有些HR面试喜欢习惯性打压？请看vcr：","6a1a90ef000000003503aa04","ABNYw0WGPP7PwQbkwmzu0-MvsQHw3mfCuSAMXsSZIqZf0=","面试被hr阴阳怪气","—"),
("如何礼貌阴阳男hr，留子和国内hr沟通被创飞","68006d90000000001e0060e4","AB52wtiI_BSQNzpsbLbNL-qw0A80yz9m55XGQi3tgCmdo=","面试被hr阴阳怪气","—"),
("救命！面试总踩坑，原来是没懂 HR 这些话背","68518792000000000c03aa2c","ABhkGh6wXMM_UuUN3Nd5e0F8mFEeH2DPhtMC6Kn_Qao4Q=","面试被hr阴阳怪气","1.5万"),
("投简历时被HR阴阳到笑不出来","680df4560000000022027fe8","ABIfg8SJxleEDJq-5xulGz_ONDWlefpJVWLo434BetnpQ=","面试被hr阴阳怪气","—"),
("🆘面试之中，遇到这些一定要主动叫停！","69fa0fd2000000003601d892","AB1NLaeWrchVTAuM6tVPcgxVPQLaPwaYwVJe96lMrHtrE=","面试被hr阴阳怪气","1977"),
("5句话，反杀阴阳你的人","6a0d85b20000000006020180","ABPcuE_PJ-3g8HlVeSNJWQrDmHRrjw3gTT3DOfumhcyP8=","面试被hr阴阳怪气","165"),
("🇨🇦刚刚结束了一场让我无地自容的面试","697a5f1d000000000e00f841","ABlzAmUXndJafN8esrtyIiU3CouR57-45uPklIwnVxffM=","面试被hr阴阳怪气","152"),
("有些女面试官的微妙恶意","69ce0c450000000022028ce5","ABWirANUTJFhvHvOjSkh3Rgf274XYflldE4h9vvj0dn4k=","面试被hr阴阳怪气","178"),

("FP&A面试高频题｜面试前一定要过一遍","6a78b194000000002500e03b","ABgC4AG4uV8pNGdRUoacucZqIzY30nvrjWRzB0nYndBVc=","求职面试高频题库","—"),
("AI Engineer面试建议这样准备","6a27d8df000000002202f518","ABjDJDJ1zmeZe6buX2OaYyTgwjCbL23gewBXpibZN3Fg4=","求职面试高频题库","—"),
("面试不要太老实，10道高频题这样答更加分","6a3c8b2e000000001702bcf7","ABv-3lRYhIDm7h69PJmm-psBbHBsJLjTZ_lVB3o6WYhUo=","求职面试高频题库","205"),
("Google高频leetcode题目(近三个月)","6a02dc6e00000000070224a5","ABkXFKZTxfjr6_TrB3sY0sOA-K5taMsyVE9QSW6hablDg=","求职面试高频题库","166"),
("dataanalyst面试不背题真的寸步难行","6779c9d10000000014023238","AB9cpYF-ZWzIveHeA2AtzN98WAacJRjF2RJ5lQSJcV-zk=","求职面试高频题库","727"),
("一图看懂job interview面试题+答案","68278c9200000000230022b9","AB0MxZw7BZ5AiI4sn2LwckawnhJqiF68WolzJjglY3_P4=","求职面试高频题库","126"),
("一个能过🇺🇸四大药厂面试的方法（附题）","6a3e428400000000220085b5","ABxm3-cF08tTEhqkuZg6DPPCynxW8zp6fJYlA2-wa2jWE=","求职面试高频题库","184"),

("老祖宗专门留下来对付小人的心理博弈术","6a2cde2d000000000e021800","ABvv6wVJ00WKzUkhM1Ny1eSJU-L4-dEA-j3QPwVRqCVbs=","高情商破局","731"),
("对付小人的唯一心法：不斗人，只破局","6a0dee580000000036000610","ABPcuE_PJ-3g8HlVeSNJWQrFDmzR8fUJjD8TgxvWbzbrk=","高情商破局","—"),
("最好的反击，是摧毁问题（第二期）","6a166f250000000037036204","AByMxq8t6-mPiSc09PYVZrXaIAhlijVratlwnInYkeDzY=","高情商破局","1.5万"),
("草台班子里最聪明的不是往上爬","6a2b4f83000000001700adfe","ABrNMG-fFQfpTgVuJ6fpDpdl4MbUMmN6_9ii6VSD2z0NY=","高情商破局","524"),
("《毛选》：别人甩锅，一招破局","6a252f680000000022019ae9","ABoGqkTEi5uSr-Aru_6YamWuXI6vzDf-i8ShZItg_xaWI=","高情商破局","—"),
("让你level飙升的8个“破局”思维","68bc37ca000000001d038d1a","ABPf5z01JGrD5vbLCjbpnpB0QivUgGIYZmj5LjFcRxpI4=","高情商破局","195"),
]

with open('职场面试_记忆库.csv','a',encoding='utf-8-sig',newline='') as f:
    w = csv.writer(f)
    for title, nid, token, kw, heat in memory_rows:
        url = f"https://www.xiaohongshu.com/search_result/{nid}?xsec_token={token}&xsec_source="
        w.writerow([title, url, "XHS", kw, TODAY, heat])

print("memory rows appended:", len(memory_rows))
