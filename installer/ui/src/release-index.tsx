import {useEffect,useState} from 'react';
import {createRoot} from 'react-dom/client';
import {CheckCircle2,FileArchive,FolderOpen,LoaderCircle,Moon,Sun,Upload} from 'lucide-react';
import {MobilePreferences} from './components/mobile-preferences';
import {MobileTools} from './components/mobile-tools';
import {Button} from './components/ui/button';
import {android,fileLabel,native,request,subscribe} from './protocol';
import {explainError} from '../../runtime/luci-app-unisoc-ota/htdocs/luci-static/resources/unisoc-ota/errors.js';
import './style.css';import './ota.css';

type Plan={batch:boolean;inputs:number;raw?:boolean};
type Result={path:string;succeeded:number;failed:Array<{input:string;error:string}>};
function IndexTool(){
 const [en,setEn]=useState(localStorage.getItem('language')==='en');
 const [dark,setDark]=useState(localStorage.getItem('theme')==='dark'||localStorage.getItem('theme')!=='light'&&matchMedia('(prefers-color-scheme:dark)').matches);
 const tr=(zh:string,english:string)=>en?english:zh;
 const [path,setPath]=useState(''),[busy,setBusy]=useState(false),[progress,setProgress]=useState(''),[error,setError]=useState('');
 const [boot,setBoot]=useState('');
 const [plan,setPlan]=useState<Plan>(),[jobs,setJobs]=useState(2),[result,setResult]=useState<Result>();
 useEffect(()=>{document.documentElement.classList.toggle('dark',dark);document.documentElement.lang=en?'en':'zh-CN';},[dark,en]);
 useEffect(()=>{if(android)document.documentElement.dataset.mobileBusy=String(busy);return()=>{if(android)delete document.documentElement.dataset.mobileBusy;};},[busy]);
 useEffect(()=>{
  const stop=subscribe(e=>{
   if(e.type==='ota_progress')setProgress(String((e.data as {path?:string}).path||''));
   else if(e.type==='import_progress') {const d=e.data as {done:number;total:number};setProgress(`${(d.done/1048576).toFixed(1)} / ${Math.max(0,d.total/1048576).toFixed(1)} MiB`);}
  });return()=>{void stop.then(f=>f());};
 },[]);
 async function pick(directory=false){
  setBusy(true);setError('');setProgress('');setResult(undefined);setBoot('');
  try{
   const selected=await native(directory?'pick_index_directory':'pick_ota_input',{kind:'index'});
   if(selected){const checked=await request<Plan>('mobile_index_plan',{path:selected});setPath(selected);setPlan(checked);}
  }catch(e){setError(String(e));}finally{setBusy(false);}
 }
 async function pickBoot(){setBusy(true);setError('');try{const selected=await native('pick_ota_input',{kind:'boot'});if(selected)setBoot(selected);}catch(e){setError(String(e));}finally{setBusy(false);}}
 async function generate(){
  setBusy(true);setError('');setResult(undefined);
  try{
   const output=await native('save_index',{suggested_name:plan?.batch?'OpenWrt-release-indexes.zip':'OpenWrt-release.owrt-index.json'});
   if(output){const completed=await request<Result>('mobile_index',{path,output,jobs,boot});setResult(completed);setProgress(tr('版本摘要已导出。','Release index exported.'));}
  }catch(e){setError(String(e));}finally{setBusy(false);}
 }
 return <><MobileTools/><div className="ota-shell"><header>
  <div className="brand"><div className="brand-icon"><FileArchive size={24}/></div><div><strong>{tr('版本摘要','Release Index')}</strong><small>{tr('为差异更新准备版本信息','Prepare release information for patch updates')}</small></div></div>
  {android?<MobilePreferences language={en?'en':'zh'} onLanguage={value=>{setEn(value==='en');localStorage.setItem('language',value);}} theme={dark?'dark':'light'} system={false} onTheme={value=>{setDark(value==='dark');localStorage.setItem('theme',value);}} disabled={busy}/>:<div className="preferences"><Button variant="ghost" disabled={busy} onClick={()=>{localStorage.setItem('language',en?'zh':'en');setEn(!en);}}>{en?'中文':'English'}</Button><Button variant="ghost" aria-label={tr('主题','Theme')} onClick={()=>{localStorage.setItem('theme',dark?'light':'dark');setDark(!dark);}}>{dark?<Sun size={18}/>:<Moon size={18}/>}</Button></div>}
 </header><main className="ota-main" aria-busy={busy}>
 <div className="ota-intro"><h1>{tr('为发行包生成摘要','Create a release index')}</h1><p>{tr('摘要可以代替 Diff Studio 的旧版完整包，方便保存和分发。支持 ZIP、已解压目录和 rootfs。','Use an index instead of the old full release in Diff Studio. Supports ZIPs, extracted folders and rootfs.')}</p></div>
 {busy&&<div className="ota-notice mobile-operation-banner" role="status"><LoaderCircle className="animate-spin" size={18}/><span>{tr('正在处理…','Processing…')}<small>{progress}</small></span></div>}
 <div className="index-workflow ota-grid"><section className="ota-card"><h2><span className="ota-number">1</span>{tr('选择输入','Choose input')}</h2>
 <div className="index-pickers"><button className="ota-picker" disabled={busy} onClick={()=>void pick()}><FileArchive/><span><strong>{tr('选择完整包或 rootfs','Release or rootfs')}</strong><small>{tr('单个文件','One file')}</small></span><FolderOpen size={18}/></button><button className="ota-picker" disabled={busy} onClick={()=>void pick(true)}><FolderOpen/><span><strong>{tr('选择目录／批量','Folder / batch')}</strong><small>{tr('已解压版本或多个发行包','An extracted release or a collection')}</small></span><FolderOpen size={18}/></button></div>
 {path&&<div className="index-selection"><strong title={path}>{fileLabel(path)}</strong>{plan&&<p>{plan.inputs} {tr('个输入','inputs')} · {plan.batch?tr('批量 ZIP 导出','Batch ZIP export'):tr('单个 JSON 导出','Single JSON export')}</p>}</div>}
 {plan?.raw&&<details className="index-boot"><summary>{tr('可选：附带旧版 boot','Optional: include old boot')}</summary><p className="index-hint">{tr('可避免将未知启动镜像视为变更。','Avoid treating an unknown boot image as changed.')}</p><div className="actions"><Button variant="outline" disabled={busy} onClick={()=>void pickBoot()}>{tr('选择 boot','Select boot')}</Button>{boot&&<Button variant="ghost" disabled={busy} onClick={()=>setBoot('')}>{tr('清除','Clear')}</Button>}</div>{boot&&<p className="file-path" title={boot}>{fileLabel(boot)}</p>}</details>}
 </section><section className="ota-card"><h2><span className="ota-number">2</span>{tr('生成并保存','Generate and save')}</h2>
 <label className="index-worker-field">{tr('并行线程','Workers')}<select value={jobs} disabled={busy} onChange={e=>setJobs(Number(e.target.value))}>{[1,2,4,8].map(n=><option key={n} value={n}>{n}</option>)}</select></label>
 <p className="index-hint">{tr('点击后选择保存位置。单个版本导出 JSON，多个版本会打包为 ZIP。','Choose a save location after tapping below. One release exports JSON; a collection exports a ZIP.')}</p>
 <Button className="ota-wide" disabled={!path||!plan||busy} onClick={()=>void generate()}>{busy?<LoaderCircle className="animate-spin" size={18}/>:<Upload size={18}/>} {tr('生成并导出','Generate and export')}</Button>
 </section></div>
 {!busy&&(progress||result)&&<div className="index-status" role="status"><p><CheckCircle2 size={18}/>{progress}</p>{result&&<p>{tr('成功','Succeeded')} {result.succeeded??1} · {tr('失败','Failed')} {result.failed?.length??0}</p>}{!!result?.failed?.length&&<details><summary>{tr('查看失败项','Failed inputs')}</summary>{result.failed.map((item,i)=><p key={i}>{fileLabel(item.input)}: {explainError(item.error,en).message}</p>)}</details>}</div>}
 {error&&<div className="ota-error" role="alert"><div><p>{explainError(error,en).message}</p><details><summary>{tr('技术详情','Technical details')}</summary><pre>{explainError(error,en).details}</pre></details></div></div>}
 </main></div></>;
}
createRoot(document.getElementById('root')!).render(<IndexTool/>);
