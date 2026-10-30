import {useEffect,useState} from 'react';
import {Archive,ArrowLeft,Download,FolderOpen,LoaderCircle,RefreshCw,Trash2,Upload} from 'lucide-react';
import {Button} from './ui/button';
import {Dialog,DialogContent,DialogDescription,DialogTitle} from './ui/dialog';
import {native,request,type Transaction} from '../protocol';
import type {Language} from '../i18n';

type Entry={record:Transaction;bytes:number;files:number;running:boolean};
type Run=<T>(work:()=>Promise<T>,message?:string)=>Promise<T|undefined>;
const bytes=(n:number)=>n>=1073741824?(n/1073741824).toFixed(2)+' GiB':n>=1048576?(n/1048576).toFixed(1)+' MiB':(n/1024).toFixed(1)+' KiB';

export function RecordManager({language,busy,run,onHome,onResume,onDeleted}:{language:Language;busy:boolean;run:Run;onHome:()=>void;onResume:()=>void;onDeleted:(id:string)=>void}) {
 const en=language==='en',tr=(zh:string,english:string)=>en?english:zh;
 const [items,setItems]=useState<Entry[]>([]),[loaded,setLoaded]=useState(false),[query,setQuery]=useState('');
 const [removing,setRemoving]=useState<Entry>(),[confirmed,setConfirmed]=useState(false);
 async function reload(){setItems(await request<Entry[]>('records'));setLoaded(true);}
 useEffect(()=>{void run(reload);},[]);
 const statuses:Record<string,string>=en?{ready:'Ready',running:'Running',waiting_user:'Waiting for confirmation',waiting_device:'Waiting for device',failed:'Failed',cancelled:'Paused',completed:'Completed'}:{ready:'待开始',running:'进行中',waiting_user:'等待确认',waiting_device:'等待设备',failed:'失败',cancelled:'已暂停',completed:'已完成'};
 const methods:Record<string,string>=en?{tf:'TF card',part:'Internal partition',reinstall:'Reinstall',image:'Userdata image',boot:'One-time boot',update:'Update',uninstall:'Uninstall'}:{tf:'TF 卡',part:'内置分区',reinstall:'重装',image:'userdata 镜像',boot:'临时启动',update:'更新',uninstall:'卸载'};
 const shown=items.filter(({record:r})=>[r.id,r.device.model,r.package.profile,r.package.release_version].join(' ').toLowerCase().includes(query.toLowerCase()));
 async function importRecord(){await run(async()=>{
  const path=await native('pick_recovery');if(!path)return false;
  await request('import',{path});await reload();return true;
 },tr('安装记录已导入','Installation record imported'));}
 async function exportRecord(entry:Entry){await run(async()=>{
  const r=entry.record,path=await native('save_backup',{suggested_name:`OpenWrt-record-${r.device.model||r.package.profile}-${r.id.slice(0,8)}.zip`});
  if(!path)return false;
  await request('export',{transaction:r.id,path});return true;
 },tr('安装记录与备份已导出','Installation record and backups exported'));}
 async function removeRecord(){if(!removing||!confirmed)return;
  const id=removing.record.id;
  const result=await run(()=>request<{deleted:string}>('delete_record',{transaction:id,confirmed:true}),tr('安装记录已删除','Installation record deleted'));
  if(result){setItems(old=>old.filter(x=>x.record.id!==id));onDeleted(id);setRemoving(undefined);setConfirmed(false);}
 }
 return <div className="record-manager">
  <h1 tabIndex={-1}>{tr('安装记录管理','Installation records')}</h1>
  <p className="intro">{tr('管理这台手机保存的安装记录与恢复备份，不会修改目标设备。','Manage installation records and recovery backups stored on this phone. These actions do not change the target device.')}</p>
  <div className="record-manager-toolbar"><Button variant="outline" disabled={busy} onClick={()=>void importRecord()}><Upload size={17}/>{tr('导入恢复备份','Import recovery backup')}</Button><Button variant="ghost" disabled={busy} onClick={()=>void run(reload)}><RefreshCw size={17}/>{tr('刷新','Refresh')}</Button></div>
  <div className="record-manager-summary"><Archive size={19}/><span>{items.length} {tr('条记录','records')} · {bytes(items.reduce((n,x)=>n+x.bytes,0))}</span>{busy&&<LoaderCircle className="spin" size={17}/>}</div>
  <label className="record-manager-search"><span className="sr-only">{tr('搜索安装记录','Search installation records')}</span><input value={query} disabled={busy} onChange={e=>setQuery(e.target.value)} placeholder={tr('搜索机型、版本或记录编号','Search model, release or record ID')}/></label>
  <div className="record-manager-list">
   {!loaded&&<p role="status" className="empty-state">{tr('正在读取记录…','Loading records…')}</p>}
   {loaded&&!shown.length&&<p className="empty-state">{query?tr('没有匹配的记录','No matching records'):tr('暂无安装记录。可以导入以前导出的恢复备份。','No installation records. You can import a previously exported recovery backup.')}</p>}
   {shown.map(entry=>{const r=entry.record;return <section className="record-manager-item" key={r.id}>
    <div className="record-manager-title"><div><h2>{r.device.model||r.package.profile}</h2><p>{methods[r.choices.method]||tr('安装','Installation')} · {new Date(r.updated*1000).toLocaleString(en?'en':'zh-CN')}</p></div><span className={'badge'+(entry.running?' running':'')}>{entry.running?tr('使用中','In use'):statuses[r.status]||tr('未知状态','Unknown state')}</span></div>
    <p className="record-manager-version">{r.package.release_version||r.package.name}</p>
    <p className="record-manager-storage">{bytes(entry.bytes)} · {entry.files} {tr('个记录及备份文件','record and backup files')}</p>
    <details className="record-manager-details"><summary>{tr('记录详情','Record details')}</summary><dl><dt>{tr('记录编号','Record ID')}</dt><dd>{r.id}</dd><dt>{tr('设备','Device')}</dt><dd>{r.device.model} · {r.device.serial}</dd><dt>{tr('系统系列','System family')}</dt><dd>{r.package.adapter.toUpperCase()} · {r.package.profile}</dd><dt>{tr('内核','Kernel')}</dt><dd>{r.package.kernel}</dd></dl></details>
    <div className="record-manager-actions"><Button variant="outline" disabled={busy||entry.running} onClick={()=>void exportRecord(entry)}><Download size={16}/>{tr('导出','Export')}</Button><Button variant="ghost" disabled={busy||entry.running} onClick={()=>{setRemoving(entry);setConfirmed(false);}}><Trash2 size={16}/>{tr('删除','Delete')}</Button></div>
   </section>;})}
  </div>
  <div className="actions"><Button variant="ghost" disabled={busy} onClick={onHome}><ArrowLeft size={16}/>{tr('返回主页','Home')}</Button><Button variant="outline" disabled={busy} onClick={onResume}><FolderOpen size={16}/>{tr('前往续装','Resume installation')}</Button></div>
  <Dialog open={!!removing} onOpenChange={open=>{if(!open&&!busy)setRemoving(undefined);}}><DialogContent closeLabel={tr('关闭','Close')}>
   <DialogTitle>{tr('删除这条安装记录？','Delete this installation record?')}</DialogTitle>
   <DialogDescription>{tr('会删除本机保存的这条记录及其目录内的恢复备份，释放','This removes the record and recovery backups in its local directory, freeing')} {bytes(removing?.bytes||0)}{tr('。不会卸载或修改目标设备。删除后将不能再用此记录续装、恢复或归还空间，请先导出需要的备份。','. It does not uninstall or change the target. You can no longer use this record to resume, recover or return space; export needed backups first.')}</DialogDescription>
   <label className="checkbox-label"><input type="checkbox" checked={confirmed} disabled={busy} onChange={e=>setConfirmed(e.target.checked)}/>{tr('我已导出或不再需要这条记录及备份','I have exported or no longer need this record and its backups')}</label>
   <div className="actions"><Button variant="outline" disabled={busy} onClick={()=>setRemoving(undefined)}>{tr('取消','Cancel')}</Button><Button variant="destructive" disabled={busy||!confirmed} onClick={()=>void removeRecord()}>{busy&&<LoaderCircle className="spin" size={16}/>} {tr('确认删除','Delete record')}</Button></div>
  </DialogContent></Dialog>
 </div>;
}
