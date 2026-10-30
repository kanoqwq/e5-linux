import {useEffect,useRef,useState} from 'react';
import {exit,onBackButtonPress} from '@tauri-apps/api/app';
import {android,request} from '../protocol';
import '../mobile.css';
import {FileArchive,GitCompareArrows,HardDrive,PackageCheck} from 'lucide-react';

export function MobileTools() {
  const [error,setError]=useState('');
  const [,refreshLanguage]=useState(0);
  const [busy,setBusy]=useState(false);
  const [pending,setPending]=useState(false);
  const returning=useRef(false);
  useEffect(()=>{
    if(!android)return;
    let disposed=false;
    const registration=onBackButtonPress(()=>{void goBack();});
    const release=()=>{if(disposed)return;disposed=true;void registration.then(listener=>listener.unregister()).catch(()=>{});};
    async function goBack() {
      if(disposed||returning.current)return;
      const dialog=document.querySelector('[role="dialog"][data-state="open"]');
      if(dialog) {
        document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',code:'Escape',bubbles:true,cancelable:true}));
        return;
      }
      returning.current=true;
      try {
        const en=localStorage.getItem('language')==='en';
        if(document.documentElement.dataset.mobileBusy==='true'||!await request<boolean>('can_close')) {
          setError(en?'Finish or safely pause the current task first.':'请先完成或安全暂停当前操作。');return;
        }
        if(disposed)return;
        const page=location.pathname.split('/').pop()||'index.html';
        if(page!=='index.html') {location.replace('index.html');return;}
        const back=new Event('openwrt:app-back',{cancelable:true});
        if(window.dispatchEvent(back))await exit(0);
      } catch(e) {if(!disposed)setError(String(e));}
      finally {returning.current=false;}
    }
    void registration.catch(e=>{if(!disposed)setError(String(e));});
    window.addEventListener('pagehide',release);
    return()=>{window.removeEventListener('pagehide',release);release();};
  },[]);
  useEffect(()=>{const observer=new MutationObserver(()=>{refreshLanguage(v=>v+1);setBusy(document.documentElement.dataset.mobileBusy==='true');});observer.observe(document.documentElement,{attributes:true,attributeFilter:['lang','data-mobile-busy']});return()=>observer.disconnect();},[]);
  if(!android)return null;
  const en=localStorage.getItem('language')==='en';
  const pages=[{page:'index.html',label:en?'Install':'安装器',full:en?'Installer':'安装器',Icon:HardDrive},
   {page:'diff.html',label:'Diff',full:'Diff Studio',Icon:GitCompareArrows},
   {page:'ota.html',label:en?'Update':'更新',full:en?'Patch Updater':'补丁更新',Icon:PackageCheck},
   {page:'release-index.html',label:en?'Index':'摘要',full:en?'Release Index':'版本摘要',Icon:FileArchive}];
  const current=location.pathname.split('/').pop()||'index.html';
  async function navigate(page:string) {
    if (busy||pending||(page===current&&page!=='index.html'))return;
    setPending(true);setError('');
    try {
      if(!await request<boolean>('can_close')) {setError(en?'Finish or safely pause the current task first.':'请先完成或安全暂停当前操作。');return;}
      if(page===current)window.dispatchEvent(new Event('openwrt:installer-home'));
      else location.href=page;
    } catch(e) {setError(String(e));}finally{setPending(false);}
  }
  return <nav className="mobile-tools" aria-label={en?'OpenWrt tools':'OpenWrt 工具集'}>
    <div className="mobile-tools-brand" aria-hidden="true"><HardDrive size={23}/><span>OpenWrt<small>TOOLS</small></span></div>
    <div className="mobile-tool-items">{pages.map(({page,label,full,Icon})=><button key={page} aria-label={full} title={full} disabled={busy||pending} aria-current={current===page?'page':undefined} onClick={()=>void navigate(page)}><span className="mobile-tool-icon"><Icon size={21}/></span><span>{label}</span></button>)}</div>
    {error&&<p role="alert" onClick={()=>setError('')}>{error}</p>}
  </nav>;
}
