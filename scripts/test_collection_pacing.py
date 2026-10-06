import asyncio,json,os,subprocess,sys,tempfile,time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import collection_pacing as p
for key, base in p.DEFAULTS.items():
 if key == 'jitter_ratio': continue
 samples = [p.random_delay(key, p.DEFAULTS) for _ in range(100)]
 assert all(base <= value <= base * 1.5 for value in samples)
 assert len(set(samples)) > 1
print('逐次随机采样、上下界与最低等待：通过')
with tempfile.TemporaryDirectory() as td:
 os.environ['XHS_PACING_DIR']=td
 settings={k:1 for k in p.DEFAULTS};settings["jitter_ratio"]=0;Path(td,'pacing.json').write_text(json.dumps(settings))
 code='''import json,time,sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); from collection_pacing import session
with session('note'):
 start=time.time();time.sleep(.05);end=time.time()
print(json.dumps([start,end]))'''
 env=os.environ.copy()
 children=[subprocess.Popen([sys.executable,'-c',code,str(ROOT / 'scripts')],stdout=subprocess.PIPE,text=True,env=env) for _ in range(3)]
 rows=sorted(json.loads(c.communicate(timeout=10)[0]) for c in children)
 assert all(b[0]-a[1]>=.98 for a,b in zip(rows,rows[1:])),rows
 print('跨进程访问串行、完成后最小间隔：通过')
 async def cancel_test():
  entered=asyncio.Event(); release=asyncio.Event()
  async def owner():
   async with p.async_session(): entered.set();await release.wait()
  async def waiter():
   async with p.async_session(): pass
  a=asyncio.create_task(owner());await entered.wait()
  b=asyncio.create_task(waiter());await asyncio.sleep(.05);b.cancel()
  try:await b
  except asyncio.CancelledError:pass
  release.set();await a
  async with p.async_session():pass
 asyncio.run(cancel_test());print('异步排队取消后释放锁：通过')
 os.environ.pop('XHS_PACING_DIR')
 env=p.opencli_env()
 r=subprocess.run(['node','-e', 'import(process.env.XHS_PACING_PAGE_MODULE).then(({Page})=>{if(!Page.prototype.goto.toString().includes("_collectionPaced"))process.exit(1);console.log("真实 opencli Page 停留钩子已加载")})'],env=env,text=True,capture_output=True)
 assert r.returncode==0,r.stderr
 print(r.stdout.strip())
