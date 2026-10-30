import {CircleHelp,EllipsisVertical,Monitor,Moon,Sun} from 'lucide-react';
import {Button} from './ui/button';

type Theme='system'|'light'|'dark';
export function MobilePreferences({language,onLanguage,theme,onTheme,system=true,onHelp,onAbout,disabled=false}:
 {language:'zh'|'en';onLanguage:(value:'zh'|'en')=>void;theme:Theme;onTheme:(value:Theme)=>void;system?:boolean;onHelp?:()=>void;onAbout?:()=>void;disabled?:boolean}) {
 const zh=language==='zh';
 return <div className="mobile-preferences">
  <label className="mobile-icon-control" title={zh?'语言':'Language'}><span aria-hidden="true">{zh?'中':'EN'}</span><select aria-label={zh?'语言':'Language'} value={language} disabled={disabled} onChange={e=>onLanguage(e.target.value as 'zh'|'en')}><option value="zh">中文</option><option value="en">English</option></select></label>
  <label className="mobile-icon-control" title={zh?'主题':'Theme'}>{theme==='dark'?<Moon size={19}/>:theme==='light'?<Sun size={19}/>:<Monitor size={19}/>}<select aria-label={zh?'主题':'Theme'} value={theme} onChange={e=>onTheme(e.target.value as Theme)}>{system&&<option value="system">{zh?'跟随系统':'System'}</option>}<option value="light">{zh?'浅色':'Light'}</option><option value="dark">{zh?'深色':'Dark'}</option></select></label>
  {onHelp&&<Button variant="ghost" className="mobile-icon-control" aria-label={zh?'帮助':'Help'} onClick={onHelp}><CircleHelp size={20}/></Button>}
  {onAbout&&<Button variant="ghost" className="mobile-icon-control" aria-label={zh?'关于与备份':'About & backups'} onClick={onAbout}><EllipsisVertical size={20}/></Button>}
 </div>;
}
