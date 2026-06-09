from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import Optional
import sqlite3
import os
import json
import shutil
import subprocess
from pathlib import Path
import uvicorn

try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False

CLAUDE_CLI = shutil.which("claude")  # None if not installed

DB_PATH = Path(__file__).parent / "pain_points.db"
INDEX_PATH = Path(__file__).parent / "index.html"

# ─────────────────────────────────────────────
# SEED DATA  (4 scenarios × 25 = 100 pain points)
# ─────────────────────────────────────────────
SEED = [
    # ── 面试 ──────────────────────────────────
    {"scenario":"面试","trigger_moment":"面试官说「介绍一下你自己」","inner_monologue":"不知道该说哪个版本，说工作经历又觉得太流水账","actual_consequence":"说了3分钟，面试官没有任何反应，直接跳下一题","severity":"高"},
    {"scenario":"面试","trigger_moment":"被追问「你在这个项目里具体做了什么」","inner_monologue":"明明做了很多，但说出来感觉都是「配合」和「参与」","actual_consequence":"面试官说「那你的贡献是什么」，脑子空白","severity":"高"},
    {"scenario":"面试","trigger_moment":"讲完一个项目，面试官沉默","inner_monologue":"不知道讲得够不够，要不要继续说","actual_consequence":"自己补充了很多多余细节，越说越乱","severity":"高"},
    {"scenario":"面试","trigger_moment":"被问「为什么从上家离职」","inner_monologue":"真实原因（老板烂/没钱途）不能说，不知道怎么讲才好听","actual_consequence":"说了个连自己都不信的理由，面试官当场追问","severity":"高"},
    {"scenario":"面试","trigger_moment":"讲项目数据被追问「这个数字怎么来的」","inner_monologue":"其实是估算的，不敢说，编又编不圆","actual_consequence":"语气变虚，面试官表情变了","severity":"高"},
    {"scenario":"面试","trigger_moment":"被问「你最大的弱点是什么」","inner_monologue":"说真实的怕减分，说假的怕被看穿","actual_consequence":"说了万年标准答案「我太追求完美」，面试官没有后续","severity":"中"},
    {"scenario":"面试","trigger_moment":"被要求「举一个失败的例子」","inner_monologue":"有案例但不知道怎么讲才不显得很差","actual_consequence":"讲到一半不敢继续，绕回去说「后来我改进了」","severity":"高"},
    {"scenario":"面试","trigger_moment":"自我介绍讲完，面试官说「还有什么要补充吗」","inner_monologue":"刚才已经说完了啊，这是什么信号","actual_consequence":"继续重复了一遍，越说越不自信","severity":"中"},
    {"scenario":"面试","trigger_moment":"讲跨部门协作，被问「你的角色是什么」","inner_monologue":"我是推动者但没有直接权力，怎么说才不显得软","actual_consequence":"用了很多「我们」，面试官问「你个人做了什么」","severity":"高"},
    {"scenario":"面试","trigger_moment":"讲技术方案时被问「为什么不用另一个方案」","inner_monologue":"当时就这么定的，没有深入比较过，无法解释","actual_consequence":"说了一堆不确定的话，面试官停下来记了什么","severity":"中"},
    {"scenario":"面试","trigger_moment":"讲完成果，面试官问「如果重来你会怎么做」","inner_monologue":"不知道说「做同样的事」好，还是说「会改变」好","actual_consequence":"两头讨好，说了个模糊答案","severity":"中"},
    {"scenario":"面试","trigger_moment":"被问「你期望的薪资是多少」","inner_monologue":"说低了吃亏，说高了被淘汰，中间那个不知道在哪","actual_consequence":"说了个区间，面试官问「最低能接受吗」","severity":"高"},
    {"scenario":"面试","trigger_moment":"面试官讲完公司情况，让你问问题","inner_monologue":"不知道问什么，问薪资太早，问项目又怕不了解情况","actual_consequence":"问了「请问团队多少人」，面试官敷衍答完沉默","severity":"中"},
    {"scenario":"面试","trigger_moment":"压力面试：「你的经历很普通，没有亮点」","inner_monologue":"知道这是测试，但内心有点慌，脑子开始空白","actual_consequence":"开始解释「其实我做过……」越解释越像在辩解","severity":"高"},
    {"scenario":"面试","trigger_moment":"被要求用英文再讲一遍刚才的回答","inner_monologue":"内容没问题，但英文组织和中文不一样，两套逻辑","actual_consequence":"英文版本比中文版本弱很多，整体印象拉低","severity":"中"},
    {"scenario":"面试","trigger_moment":"被问「你对这个行业有什么判断」","inner_monologue":"有看法，但不确定会不会踩到面试官的立场","actual_consequence":"说了个四平八稳的答案，面试官兴趣下降","severity":"中"},
    {"scenario":"面试","trigger_moment":"讲完亮点，面试官说「就这些？」","inner_monologue":"不知道是已经够了，还是需要继续加料","actual_consequence":"继续说，把一个好的回答稀释了","severity":"高"},
    {"scenario":"面试","trigger_moment":"被问「你为什么想来我们公司」","inner_monologue":"真实原因是薪资高/在家附近，但说出来不合适","actual_consequence":"说了网上搜来的公司介绍，面试官听起来漫不经心","severity":"中"},
    {"scenario":"面试","trigger_moment":"技术面讲完方案后被当场挑战","inner_monologue":"知道这个方案有取舍，但被挑战时不知道如何有结构地回应","actual_consequence":"反应慢，沉默了5秒，面试官主导了后续","severity":"中"},
    {"scenario":"面试","trigger_moment":"面试最后被问「还有什么问题吗」","inner_monologue":"没有问题，但说「没有问题」感觉太仓促","actual_consequence":"临时问了个显得没准备的问题，结尾感觉很弱","severity":"低"},
    {"scenario":"面试","trigger_moment":"分享成功案例，被问「那是运气还是能力」","inner_monologue":"这是陷阱，怎么回答都有风险","actual_consequence":"说「两者都有」，没法体现自己的判断力","severity":"高"},
    {"scenario":"面试","trigger_moment":"被问「你在团队里是什么角色」","inner_monologue":"做的事很多，但说「什么都做」显得没有核心","actual_consequence":"列了一堆职责，面试官打断「你的核心价值是什么」","severity":"高"},
    {"scenario":"面试","trigger_moment":"整场面试结束，面试官说「我们会通知你」","inner_monologue":"不知道是客套话还是真有意向，要不要追问","actual_consequence":"沉默离开，失去了最后一个主动加分的机会","severity":"低"},
    {"scenario":"面试","trigger_moment":"被追问「你当时做这个决定的原因是什么」","inner_monologue":"决定是集体拍的，我只是执行者，怎么说才不显得被动","actual_consequence":"说了「当时团队一起讨论的」，面试官继续追「你的判断呢」","severity":"高"},
    {"scenario":"面试","trigger_moment":"面试过了三轮，最终面时被问「你和其他候选人有什么不同」","inner_monologue":"我不知道其他人是谁，怎么比较","actual_consequence":"说了一些通用优势，面试官表情没变化，没有追问","severity":"高"},

    # ── 谈薪 ──────────────────────────────────
    {"scenario":"谈薪","trigger_moment":"收到口头offer，HR说「薪资方面你有什么期望」","inner_monologue":"说低了吃亏，说高了被拒，不知道市场价在哪","actual_consequence":"报了比现有高20%的数字，HR说「超出范围」，谈判陷入僵局","severity":"高"},
    {"scenario":"谈薪","trigger_moment":"HR给出一个数字，说「这是我们能给的上限」","inner_monologue":"这真的是上限吗，还是还有空间但在压价","actual_consequence":"直接接受，事后发现同岗位同学薪资高出30%","severity":"高"},
    {"scenario":"谈薪","trigger_moment":"被问「你目前薪资是多少」","inner_monologue":"要如实说吗，说低了影响报价，说高了要流水证明","actual_consequence":"报了真实数字，offer只比现在高10%","severity":"高"},
    {"scenario":"谈薪","trigger_moment":"谈到薪资，对方问「你有多个offer吗」","inner_monologue":"有但只有一个，说没有会不会失去谈判筹码","actual_consequence":"说没有，对方报价直接压低","severity":"高"},
    {"scenario":"谈薪","trigger_moment":"涨幅没达到预期，HR说「公司规定最高只能涨X%」","inner_monologue":"这个规定是真实的还是托辞，要怎么绕过去","actual_consequence":"接受了，没有争取股权/奖金等其他补偿形式","severity":"高"},
    {"scenario":"谈薪","trigger_moment":"HR说「我们看重长期发展，薪资不是最重要的」","inner_monologue":"这句话是什么意思，要怎么回应才不显得太在乎钱","actual_consequence":"被带偏，开始谈「发展」，忘记继续推薪资","severity":"高"},
    {"scenario":"谈薪","trigger_moment":"已经口头接受了，HR还没发offer就说「我们再谈谈薪资吧」","inner_monologue":"我已经答应了，现在谈会不会显得反复无常","actual_consequence":"没有再谈，白白损失了一次counter的机会","severity":"中"},
    {"scenario":"谈薪","trigger_moment":"年底绩效A，但加薪幅度只有5%","inner_monologue":"我知道应该争取，但不知道怎么开口","actual_consequence":"沉默接受，第二年继续重演","severity":"高"},
    {"scenario":"谈薪","trigger_moment":"和老板约谈加薪，老板第一句话是「公司今年很难」","inner_monologue":"这是在给我打预防针，还是在开谈判","actual_consequence":"同情老板，主动降低期望，连要求都没说出口","severity":"高"},
    {"scenario":"谈薪","trigger_moment":"对方给出base+期权组合，说「综合年包很不错」","inner_monologue":"期权到底能值多少，怎么对比现在的base","actual_consequence":"被期权数字迷惑，接受了低于市场的base","severity":"高"},
    {"scenario":"谈薪","trigger_moment":"HR说「我们是16薪」","inner_monologue":"16薪是月薪低一点，还是真的更划算","actual_consequence":"没有换算清楚就接受了，到手比预期少","severity":"中"},
    {"scenario":"谈薪","trigger_moment":"谈薪到deadline，HR说「今天能给我答复吗」","inner_monologue":"时间压力下要不要继续争，还是先答应再说","actual_consequence":"在压力下接受了，没有再push一轮","severity":"高"},
    {"scenario":"谈薪","trigger_moment":"面试后收到offer，比预期低20%","inner_monologue":"要直接拒绝，还是说理由再争争","actual_consequence":"直接说「太低了」，对方不继续谈，机会没了","severity":"高"},
    {"scenario":"谈薪","trigger_moment":"HR发来书面offer，某些条款不清楚","inner_monologue":"要不要问，问了会不会显得挑剔，影响最终录用","actual_consequence":"没问，入职后发现某个条款不符合预期","severity":"中"},
    {"scenario":"谈薪","trigger_moment":"被问「你的底薪是多少，奖金是多少」","inner_monologue":"要不要把奖金说高一点，用来抬高总包基数","actual_consequence":"如实说了，对方报价只参照了base，没考虑奖金","severity":"高"},
    {"scenario":"谈薪","trigger_moment":"谈到股权，HR说「这个我没有权限谈」","inner_monologue":"到底要不要继续push，还是算了","actual_consequence":"放弃了，其实换hiring manager谈是可以的","severity":"中"},
    {"scenario":"谈薪","trigger_moment":"HR说「你的期望超出了JD的range」","inner_monologue":"JD写的薪资范围是固定的吗，还是只是起点","actual_consequence":"主动降低了期望，没有争取「看是否有例外」","severity":"高"},
    {"scenario":"谈薪","trigger_moment":"准备谈薪，但不知道自己的市场价是多少","inner_monologue":"同行薪资哪里查，说了个数字怕不专业","actual_consequence":"报了个感觉「差不多」的数字，不知道是高还是低","severity":"高"},
    {"scenario":"谈薪","trigger_moment":"HR问「你的上家薪资流水可以提供吗」","inner_monologue":"不提供会不会被pass，提供了薪资不高怎么办","actual_consequence":"提供了流水，成了对方压价的依据","severity":"高"},
    {"scenario":"谈薪","trigger_moment":"谈薪时HR说「同岗位的人都在这个范围」","inner_monologue":"这是真实的市场情况，还是在统一报价","actual_consequence":"相信了，没有争，实际上有人谈到更高","severity":"中"},
    {"scenario":"谈薪","trigger_moment":"已入职，发现新来同事薪资比自己高","inner_monologue":"要不要跟老板谈，怎么谈才不显得小气","actual_consequence":"憋了6个月没开口，对公司彻底失去信心","severity":"高"},
    {"scenario":"谈薪","trigger_moment":"谈薪时对方问「你除了薪资还在意什么」","inner_monologue":"说关心发展会不会让薪资谈判就此结束","actual_consequence":"被转移话题，全程谈「发展」，薪资没有继续推","severity":"高"},
    {"scenario":"谈薪","trigger_moment":"谈判到最后，HR说「我帮你再去申请一下」","inner_monologue":"这句话是真的在争取，还是客套话","actual_consequence":"就这么等着，没有后续跟进，最后数字没变","severity":"中"},
    {"scenario":"谈薪","trigger_moment":"跳槽谈完薪资，原公司立刻跟进加薪留人","inner_monologue":"怎么在两个offer之间做决策，还是能用来再谈","actual_consequence":"慌乱中做了决定，事后后悔","severity":"高"},
    {"scenario":"谈薪","trigger_moment":"收到offer，发现转正薪资比试用期高很多","inner_monologue":"这个结构合理吗，怎么谈转正薪资","actual_consequence":"没注意到，入职后发现差额不小，后悔没早谈","severity":"中"},

    # ── 汇报 ──────────────────────────────────
    {"scenario":"汇报","trigger_moment":"周会轮到自己汇报，leader问「进展怎么样」","inner_monologue":"说了一堆细节，但不知道leader真正想听什么","actual_consequence":"说了5分钟，leader没有任何反应，直接说「好，下一个」","severity":"高"},
    {"scenario":"汇报","trigger_moment":"汇报项目进度，实际上滞后了","inner_monologue":"说滞后会被问原因，说「正常推进」又是在骗","actual_consequence":"模糊表达，leader后来自己发现，信任感受损","severity":"高"},
    {"scenario":"汇报","trigger_moment":"PPT汇报完，leader问「你的建议是什么」","inner_monologue":"我就是来汇报情况的，没准备建议","actual_consequence":"说了个模糊方向，leader当场否定，很尴尬","severity":"高"},
    {"scenario":"汇报","trigger_moment":"汇报遇到leader质疑「这个方案有没有考虑过XX风险」","inner_monologue":"这个风险我没想到，要不要承认","actual_consequence":"开始解释「其实也有考虑」，漏洞越说越多","severity":"高"},
    {"scenario":"汇报","trigger_moment":"向老板汇报一个坏消息（项目出了问题）","inner_monologue":"要先说什么，不知道先说问题还是先说解决方案","actual_consequence":"把坏消息说出来，老板沉默了，不知道怎么接","severity":"高"},
    {"scenario":"汇报","trigger_moment":"跨部门汇报，不同stakeholder关注点不同","inner_monologue":"到底要准备一份PPT还是两份，重点放在哪里","actual_consequence":"准备了大而全的内容，每个人只关注一小部分","severity":"中"},
    {"scenario":"汇报","trigger_moment":"汇报时被打断，对方说「直接说结论」","inner_monologue":"我的铺垫很重要，没有背景会听不懂结论","actual_consequence":"跳到结论，对方说「你怎么没说清楚来龙去脉」","severity":"高"},
    {"scenario":"汇报","trigger_moment":"述职，需要展示这一年的成果","inner_monologue":"做了很多事，但说出来感觉都是执行层，没有亮点","actual_consequence":"流水账式陈列，最后得了个「表现一般」的评价","severity":"高"},
    {"scenario":"汇报","trigger_moment":"跟leader说「我有一个想法想和你说」","inner_monologue":"想法说出来，leader会不会觉得不成熟","actual_consequence":"前言太多，leader不耐烦，没有听完就结束对话","severity":"高"},
    {"scenario":"汇报","trigger_moment":"汇报中leader问「这是谁决定的」","inner_monologue":"说是我决定的会被追责，说「大家一起」又像在推卸","actual_consequence":"用了「我们团队」，leader说「具体是谁」，沉默了","severity":"高"},
    {"scenario":"汇报","trigger_moment":"汇报数据，被问「和上个季度相比怎么样」","inner_monologue":"我没有准备同比数据，临时算又慢","actual_consequence":"说「我回头发给你」，专业度大打折扣","severity":"中"},
    {"scenario":"汇报","trigger_moment":"月度汇报，成果不突出","inner_monologue":"要不要用一些模糊表达来美化数字","actual_consequence":"包装过度，leader直接问「这个数字怎么算出来的」","severity":"高"},
    {"scenario":"汇报","trigger_moment":"向上汇报，leader当场问了超出汇报范围的问题","inner_monologue":"我不知道，但当着大家说「不知道」很尴尬","actual_consequence":"绕了半天没正面回答，显得心虚","severity":"中"},
    {"scenario":"汇报","trigger_moment":"汇报结束，leader说「下周再汇报一次」","inner_monologue":"下周该怎么调整，这次哪里不对我还没搞清楚","actual_consequence":"下周汇报了个差不多的版本，再次被打回","severity":"高"},
    {"scenario":"汇报","trigger_moment":"汇报时leader的注意力在手机上","inner_monologue":"他没在听，要不要直接点他，还是继续说","actual_consequence":"继续说完，leader说「再简单说一下核心点」，等于重来","severity":"中"},
    {"scenario":"汇报","trigger_moment":"年终述职汇报给跳级上级（没见过几次的VP）","inner_monologue":"VP不了解我的日常工作，要从多基础开始讲","actual_consequence":"铺垫太多，VP打断：「你直接说对公司的价值」","severity":"高"},
    {"scenario":"汇报","trigger_moment":"被要求在大会上做5分钟汇报","inner_monologue":"时间短，不知道取舍，什么都想说","actual_consequence":"超时了，被打断，最重要的结论没讲到","severity":"高"},
    {"scenario":"汇报","trigger_moment":"汇报时顺带提了一个需要资源支持的请求","inner_monologue":"资源请求要放在哪里说，放最后会不会被忽略","actual_consequence":"放在最后说，leader已经去下个会，资源请求被忘掉","severity":"高"},
    {"scenario":"汇报","trigger_moment":"汇报一件复杂的事，leader说「说人话」","inner_monologue":"我讲的是业务语言，怎么让外行也能听懂","actual_consequence":"换了表达，但丢失了关键细节，leader又说「太简单」","severity":"中"},
    {"scenario":"汇报","trigger_moment":"汇报时发现PPT有错误","inner_monologue":"要在台上承认吗，还是跳过","actual_consequence":"硬撑，后来有人指出来，当场很尴尬","severity":"中"},
    {"scenario":"汇报","trigger_moment":"项目结束做复盘汇报，结果不理想","inner_monologue":"要不要在复盘里把锅分给其他部门","actual_consequence":"说了团队协作问题，另一部门leader当场反驳","severity":"高"},
    {"scenario":"汇报","trigger_moment":"汇报前leader说「10分钟够了吗」","inner_monologue":"我准备了30分钟的内容，怎么删","actual_consequence":"压缩成10分钟，核心逻辑被打乱，结构混乱","severity":"中"},
    {"scenario":"汇报","trigger_moment":"汇报时被stakeholder当场反对","inner_monologue":"要在台上怼回去，还是先接受再私下解决","actual_consequence":"当场怼了，气氛变僵，最后什么决定都没做出来","severity":"高"},
    {"scenario":"汇报","trigger_moment":"例会前没准备，临时被点名汇报","inner_monologue":"没有数据，没有结论，要硬撑还是坦白没准备","actual_consequence":"说了些模糊的「进展顺利」，leader当场问「给个具体数字」","severity":"高"},
    {"scenario":"汇报","trigger_moment":"汇报后leader说「你下次先跟我对一下再汇报给大家」","inner_monologue":"这是批评吗，还是好意提醒","actual_consequence":"以为是批评，下次过度对齐，真正的内容反而被稀释","severity":"中"},

    # ── 向上管理 ──────────────────────────────
    {"scenario":"向上管理","trigger_moment":"有想法需要leader拍板，但leader一直推迟","inner_monologue":"怎么催而不显得在施压","actual_consequence":"事情被搁置，变成自己的延误责任","severity":"高"},
    {"scenario":"向上管理","trigger_moment":"leader交代了一件事，但没有说清楚目标","inner_monologue":"要不要问，问了会不会显得理解能力差","actual_consequence":"按自己理解做了，结果和leader预期完全不同","severity":"高"},
    {"scenario":"向上管理","trigger_moment":"发现leader做了一个决定，你觉得是错的","inner_monologue":"要不要说，说了会不会被认为在挑战权威","actual_consequence":"没说，照做了，出了问题被问「你为什么不提出来」","severity":"高"},
    {"scenario":"向上管理","trigger_moment":"leader当着团队说了你的不是","inner_monologue":"当场回应会很尴尬，但不回应感觉是在认错","actual_consequence":"沉默了，团队以为你真的犯错，形象受损","severity":"高"},
    {"scenario":"向上管理","trigger_moment":"你做的项目，leader在向上汇报时把功劳归到自己名下","inner_monologue":"说出来显得小气，但不说永远没人知道是我做的","actual_consequence":"沉默，此后每次都重演，开始不愿意做事","severity":"高"},
    {"scenario":"向上管理","trigger_moment":"向leader请求更多资源，被拒绝","inner_monologue":"是直接接受，还是再push一次，怎么push才不显得无理取闹","actual_consequence":"接受了，项目因资源不足延期，又被追责","severity":"高"},
    {"scenario":"向上管理","trigger_moment":"leader的目标和你理解的完全不一样","inner_monologue":"要不要在大会上指出来，还是私下说","actual_consequence":"私下说了，leader说「我说的就是这个意思」，问题没解决","severity":"高"},
    {"scenario":"向上管理","trigger_moment":"领到的任务不合理（范围太大/时间太短）","inner_monologue":"如果说做不到，会不会显得能力不行","actual_consequence":"承接了，做到一半崩了，leader怪你没有提前说","severity":"高"},
    {"scenario":"向上管理","trigger_moment":"leader在会议上支持了竞争对手的方案而不是你的","inner_monologue":"要不要会后解释自己的方案，还是接受结果","actual_consequence":"没有解释，后来leader发现你的方案其实更好，但机会已过","severity":"中"},
    {"scenario":"向上管理","trigger_moment":"你比leader更了解某个专业细节，leader在大会上说错了","inner_monologue":"当着大家纠正leader会不会显得不给面子","actual_consequence":"没纠正，错误信息传到上面，leader被质疑，反而怪你没说","severity":"高"},
    {"scenario":"向上管理","trigger_moment":"发现自己被同事越级举报了一件事","inner_monologue":"要不要主动找leader解释，还是等leader来找我","actual_consequence":"等leader来找，被认为没有主动管理信息差","severity":"高"},
    {"scenario":"向上管理","trigger_moment":"leader给了你一个你认为不该做的任务","inner_monologue":"怎么拒绝而不影响评价","actual_consequence":"接受了，把自己压垮，KPI全部受影响","severity":"高"},
    {"scenario":"向上管理","trigger_moment":"你对leader的管理方式有意见（比如信息不透明）","inner_monologue":"说了会不会留下「不好管理」的印象","actual_consequence":"没说，积怨变深，最终直接离职","severity":"高"},
    {"scenario":"向上管理","trigger_moment":"leader说「你最近状态不太好」","inner_monologue":"不知道他具体观察到了什么，要如何回应","actual_consequence":"说「没有，我很好」，leader以为在辩解，关系变远","severity":"中"},
    {"scenario":"向上管理","trigger_moment":"向leader提了一个新想法，被直接否定","inner_monologue":"是就这样放弃，还是再争取一次","actual_consequence":"放弃了，一个月后同事提了类似想法被采纳了","severity":"高"},
    {"scenario":"向上管理","trigger_moment":"绩效评价低于预期，和leader的感知不同","inner_monologue":"要不要当场质疑，还是接受后再沟通","actual_consequence":"当场质疑，leader认为在「无理取闹」，关系受损","severity":"高"},
    {"scenario":"向上管理","trigger_moment":"leader让你去协调另一个部门，但你没有影响力","inner_monologue":"要怎么借leader的权威去推动，而不显得在甩锅","actual_consequence":"自己去谈，被对方推掉，回来跟leader说「他们不配合」","severity":"高"},
    {"scenario":"向上管理","trigger_moment":"有职业发展诉求（想转岗/晋升），不知道怎么和leader说","inner_monologue":"说早了会不会显得不安分，说晚了就没有位置了","actual_consequence":"一直等「合适的时机」，机会被别人抢走","severity":"高"},
    {"scenario":"向上管理","trigger_moment":"leader的风格很细节控，总是微管理","inner_monologue":"要反馈这件事，还是适应这种风格","actual_consequence":"默默承受，工作满意度持续下降，效率也下降","severity":"中"},
    {"scenario":"向上管理","trigger_moment":"发现leader在关键决策上听错了信息","inner_monologue":"要纠正，但信息来自更高层，纠正会不会得罪人","actual_consequence":"没纠正，决策执行后失败，leader很难看","severity":"高"},
    {"scenario":"向上管理","trigger_moment":"年终写自评，不知道如何突出自己的贡献","inner_monologue":"写太谦虚没用，写太自夸显得吹牛","actual_consequence":"写了一堆执行细节，而不是业务影响，结果平平","severity":"中"},
    {"scenario":"向上管理","trigger_moment":"leader给的反馈太模糊，说「你要更有主人翁精神」","inner_monologue":"这话什么意思，要怎么改","actual_consequence":"什么都没改，下次review又说同样的话","severity":"中"},
    {"scenario":"向上管理","trigger_moment":"和leader意见分歧，在会议上僵持了","inner_monologue":"继续争会伤感情，放弃又不甘心","actual_consequence":"在没有结论的情况下散会，决策被搁置","severity":"高"},
    {"scenario":"向上管理","trigger_moment":"发现leader对你的工作认知有偏差（认为你只负责执行）","inner_monologue":"要怎么纠正这个认知，同时不显得在邀功","actual_consequence":"没纠正，晋升时被认为「缺乏战略视角」","severity":"高"},
    {"scenario":"向上管理","trigger_moment":"新leader上任，和原来的工作方式完全不同","inner_monologue":"要主动适应还是等他摸清楚情况再说","actual_consequence":"继续用老方式工作，新leader认为你「不配合变化」","severity":"高"},
]

# ─────────────────────────────────────────────
# DATABASE
# ─────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pain_points (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scenario TEXT NOT NULL,
            trigger_moment TEXT NOT NULL,
            inner_monologue TEXT NOT NULL,
            actual_consequence TEXT NOT NULL,
            severity TEXT NOT NULL,
            status TEXT DEFAULT '未使用',
            content_url TEXT DEFAULT '',
            source TEXT DEFAULT 'ai_generated',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    count = conn.execute("SELECT COUNT(*) FROM pain_points").fetchone()[0]
    if count == 0:
        for p in SEED:
            conn.execute(
                "INSERT INTO pain_points (scenario,trigger_moment,inner_monologue,actual_consequence,severity,source) VALUES (?,?,?,?,?,?)",
                (p["scenario"],p["trigger_moment"],p["inner_monologue"],p["actual_consequence"],p["severity"],"ai_generated")
            )
    conn.commit()
    conn.close()

def row_to_dict(row):
    return dict(row)

# ─────────────────────────────────────────────
# FASTAPI
# ─────────────────────────────────────────────
app = FastAPI(title="职场表达力·痛点库")

class PainPointCreate(BaseModel):
    scenario: str
    trigger_moment: str
    inner_monologue: str
    actual_consequence: str
    severity: str
    source: Optional[str] = "manual"

class PainPointUpdate(BaseModel):
    status: Optional[str] = None
    content_url: Optional[str] = None
    trigger_moment: Optional[str] = None
    inner_monologue: Optional[str] = None
    actual_consequence: Optional[str] = None
    severity: Optional[str] = None

class ExtractRequest(BaseModel):
    content: str

@app.on_event("startup")
def startup():
    init_db()

@app.get("/", response_class=HTMLResponse)
def serve_frontend():
    return INDEX_PATH.read_text(encoding="utf-8")

@app.get("/api/pain-points")
def list_pain_points(
    scenario: Optional[str] = None,
    severity: Optional[str] = None,
    status: Optional[str] = None,
    q: Optional[str] = None,
):
    conn = get_db()
    sql = "SELECT * FROM pain_points WHERE 1=1"
    params = []
    if scenario:
        sql += " AND scenario=?"; params.append(scenario)
    if severity:
        sql += " AND severity=?"; params.append(severity)
    if status:
        sql += " AND status=?"; params.append(status)
    if q:
        sql += " AND (trigger_moment LIKE ? OR inner_monologue LIKE ? OR actual_consequence LIKE ?)"
        params += [f"%{q}%", f"%{q}%", f"%{q}%"]
    sql += " ORDER BY created_at DESC"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [row_to_dict(r) for r in rows]

@app.post("/api/pain-points", status_code=201)
def create_pain_point(body: PainPointCreate):
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO pain_points (scenario,trigger_moment,inner_monologue,actual_consequence,severity,source) VALUES (?,?,?,?,?,?)",
        (body.scenario, body.trigger_moment, body.inner_monologue, body.actual_consequence, body.severity, body.source)
    )
    conn.commit()
    row = conn.execute("SELECT * FROM pain_points WHERE id=?", (cur.lastrowid,)).fetchone()
    conn.close()
    return row_to_dict(row)

@app.put("/api/pain-points/{pid}")
def update_pain_point(pid: int, body: PainPointUpdate):
    conn = get_db()
    row = conn.execute("SELECT id FROM pain_points WHERE id=?", (pid,)).fetchone()
    if not row:
        conn.close(); raise HTTPException(404, "Not found")
    fields, vals = [], []
    for k, v in body.dict(exclude_none=True).items():
        fields.append(f"{k}=?"); vals.append(v)
    if fields:
        conn.execute(f"UPDATE pain_points SET {', '.join(fields)} WHERE id=?", vals + [pid])
        conn.commit()
    row = conn.execute("SELECT * FROM pain_points WHERE id=?", (pid,)).fetchone()
    conn.close()
    return row_to_dict(row)

@app.delete("/api/pain-points/{pid}", status_code=204)
def delete_pain_point(pid: int):
    conn = get_db()
    conn.execute("DELETE FROM pain_points WHERE id=?", (pid,))
    conn.commit()
    conn.close()

@app.get("/api/stats")
def stats():
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) FROM pain_points").fetchone()[0]
    by_scenario = {r[0]: r[1] for r in conn.execute("SELECT scenario, COUNT(*) FROM pain_points GROUP BY scenario").fetchall()}
    by_severity = {r[0]: r[1] for r in conn.execute("SELECT severity, COUNT(*) FROM pain_points GROUP BY severity").fetchall()}
    by_status   = {r[0]: r[1] for r in conn.execute("SELECT status, COUNT(*) FROM pain_points GROUP BY status").fetchall()}
    conn.close()
    return {"total": total, "by_scenario": by_scenario, "by_severity": by_severity, "by_status": by_status}

def _parse_json_response(raw: str):
    raw = raw.strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1] if len(parts) > 1 else raw
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()
    items = json.loads(raw)
    return items if isinstance(items, list) else [items]


def _extract_via_cli(prompt: str) -> str:
    result = subprocess.run(
        [CLAUDE_CLI, "-p", prompt],
        capture_output=True, text=True, timeout=120,
        env={**os.environ, "TERM": "dumb"},
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "claude CLI 返回非零退出码")
    return result.stdout.strip()


def _extract_via_sdk(prompt: str) -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("请设置环境变量 ANTHROPIC_API_KEY，或确保 claude CLI 已安装")
    client = anthropic.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text.strip()


@app.post("/api/extract")
def extract(body: ExtractRequest):
    prompt = f"""你是职场表达力内容分析专家。请从以下内容中提炼职场人的表达痛点。

返回纯 JSON 数组（不要 markdown 代码块，不要其他文字），每个对象格式：
{{
  "scenario": "面试或谈薪或汇报或向上管理（选一个最匹配的）",
  "trigger_moment": "触发时刻——什么具体情境下卡壳了（20-40字）",
  "inner_monologue": "内心独白——当时脑子里在想什么（20-40字）",
  "actual_consequence": "实际后果——卡壳之后发生了什么（20-40字）",
  "severity": "高或中或低（高=影响offer/决策，中=影响印象，低=影响加分）"
}}

专注在「说错/说少/说不出」三类表达问题上。

待分析内容：
{body.content}"""

    raw = None
    last_err = None

    # 优先走 Claude Code CLI（复用登录态，无需 API Key）
    if CLAUDE_CLI:
        try:
            raw = _extract_via_cli(prompt)
        except Exception as e:
            last_err = str(e)

    # Fallback：Anthropic SDK
    if raw is None:
        if not ANTHROPIC_AVAILABLE:
            raise HTTPException(500, f"claude CLI 不可用（{last_err}），且未安装 anthropic SDK")
        try:
            raw = _extract_via_sdk(prompt)
        except Exception as e:
            raise HTTPException(500, str(e))

    try:
        items = _parse_json_response(raw)
        return {"items": items}
    except Exception:
        return {"items": [], "raw": raw, "error": "JSON解析失败，请查看raw字段"}

if __name__ == "__main__":
    print("\n  职场表达力·痛点库")
    print("  http://localhost:8000\n")
    uvicorn.run(app, host="0.0.0.0", port=8000)
