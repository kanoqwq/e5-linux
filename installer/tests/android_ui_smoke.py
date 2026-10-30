"""Production web assets under an Android user agent with a fake native plugin."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright
from ui_smoke import BRIDGE,ROOT

MOBILE=BRIDGE+r'''
localStorage.setItem('onboarding-v1','seen');
const desktopInvoke=window.__TAURI_INTERNALS__.invoke;
window.__TAURI_INTERNALS__.invoke=async(command,args={})=>{
 if(command==='plugin:mobile-host|register_listener') {window.__mobileEvent=args.handler.onmessage;return null;}
 if(command==='plugin:mobile-host|remove_listener')return null;
 if(command==='plugin:mobile-host|request'){
  const params=JSON.parse(args.parameters);
  if(args.command==='can_close')return {result:true};
  if(args.command==='mobile_index')return {result:{path:params.output}};
  if(args.command==='mobile_index_plan')return {result:{batch:false,inputs:1}};
  if(args.command==='ota_package')throw new Error('Choose a patch / 请选择补丁');
  return {result:await desktopInvoke('backend_request',{command:args.command,params})};
 }
 if(command==='plugin:mobile-host|native_command'){
  if(args.command==='pick_package')return {value:'/phone/package'};
  if(args.command==='pick_ota_input')return {value:'/phone/release.zip'};
  if(args.command==='save_index')return {value:'/phone/index.json'};
  return {value:null};
 }
 throw new Error('Unexpected desktop command on Android: '+command);
};
'''


def main():
    report=[]
    with sync_playwright() as p:
        browser=p.chromium.launch()
        for language in ('zh','en'):
            for size in ({'width':360,'height':740},{'width':393,'height':851},{'width':844,'height':390},{'width':800,'height':1280},{'width':1280,'height':800}):
                context=browser.new_context(viewport=size,user_agent='Mozilla/5.0 (Linux; Android 15) AppleWebKit/537.36 Chrome/130.0 Mobile Safari/537.36',is_mobile=True,has_touch=True)
                context.route('https://api.kanokano.cn/**',lambda r:r.abort())
                context.add_init_script(MOBILE+f"\nlocalStorage.setItem('language',{json.dumps(language)});")
                page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                for entry in ('index.html','diff.html','ota.html','release-index.html'):
                    page.goto('http://127.0.0.1:1420/'+entry);page.wait_for_timeout(500)
                    assert page.locator('html.android-host').count()==1
                    assert page.locator('.mobile-tools button').count()==4
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),(language,size,entry)
                    assert page.locator('main').bounding_box()['height']>100,(language,size,entry)
                    metrics=page.evaluate("""() => {
                      const nav=document.querySelector('.mobile-tools').getBoundingClientRect();
                      const head=document.querySelector('header').getBoundingClientRect();
                      const main=document.querySelector('main').getBoundingClientRect();
                      return {nav:{x:nav.x,y:nav.y,right:nav.right,bottom:nav.bottom},head:{x:head.x,right:head.right,bottom:head.bottom},main:{x:main.x,bottom:main.bottom},body:document.body.scrollHeight,viewport:innerHeight};
                    }""")
                    assert metrics['head']['right']<=size['width']+1,(entry,metrics)
                    if size['width']<768:
                        assert metrics['nav']['y']>=metrics['main']['bottom']-1,(entry,metrics)
                        assert metrics['head']['bottom']<100,(entry,metrics)
                    else:
                        assert metrics['nav']['right']<=metrics['main']['x']+1,(entry,metrics)
                    assert metrics['body']<=size['height']+1,(entry,metrics)
                    if entry=='index.html':
                        assert page.locator('.mobile-flowbar').count()==0
                        assert page.locator('.purpose').count()>=4
                        assert page.locator('.purpose-list').evaluate("e=>getComputedStyle(e).overflowY")=='visible'
                        page.locator('.purpose').first.click()
                        page.locator('.mobile-flowbar').wait_for()
                        assert page.locator('.mobile-step-details').get_attribute('open') is None
                        main=page.locator('main')
                        main.evaluate('e=>e.scrollTop=e.scrollHeight')
                        assert main.evaluate('e=>e.scrollHeight<=e.clientHeight||e.scrollTop>0'),(language,size)
                        main.evaluate('e=>e.scrollTop=0')
                        page.locator('.mobile-step-details summary').click()
                        assert page.locator('.mobile-step-details .stepper').is_visible()
                        page.locator('.mobile-step-details summary').click()
                        page.locator('.mobile-home-action').click()
                        page.locator('.purpose').first.wait_for()
                        page.wait_for_timeout(400)
                    if entry=='release-index.html':
                        page.get_by_role('button',name='选择完整包或 rootfs 单个文件' if language=='zh' else 'Release or rootfs One file',exact=True).click()
                        page.get_by_role('button',name='生成并导出' if language=='zh' else 'Generate and export',exact=True).click()
                        page.get_by_role('status').get_by_text('版本摘要已导出。' if language=='zh' else 'Release index exported.',exact=True).wait_for()
                    if language=='zh' and size['width']==393:
                        target=ROOT/'verification';target.mkdir(exist_ok=True)
                        page.screenshot(path=str(target/('android-'+entry.replace('.html','.png'))))
                    if language=='zh' and size['width']==1280:
                        page.screenshot(path=str(ROOT/'verification'/('android-tablet-'+entry.replace('.html','.png'))))
                    report.append({'language':language,'viewport':size,'page':entry,'passed':True})
                assert not errors,errors
                context.close()
        browser.close()
    (ROOT/'verification/android-ui-regression.json').write_text(json.dumps(report,indent=2)+'\n')
    print('Android production UI: 40 page/language/viewport checks passed; no device access')


if __name__=='__main__':main()
