"""Mobile dialogs: production assets and real layout, synthetic backend only."""
import json
from playwright.sync_api import sync_playwright
from android_ui_smoke import MOBILE,ROOT


def check(page,kind,size):
    dialog=page.locator('[role=dialog]')
    dialog.wait_for();page.wait_for_timeout(250)
    dimensions=dialog.evaluate("""el=>{
      const r=el.getBoundingClientRect(),body=el.querySelector('.help-article,.onboarding-body,.support-body,.mobile-about-body'),footer=el.querySelector('.dialog-footer');
      const f=footer.getBoundingClientRect();
      return {x:r.x,y:r.y,right:r.right,bottom:r.bottom,width:r.width,outer:el.scrollWidth,inner:el.clientWidth,bodyHeight:body.clientHeight,bodyScroll:getComputedStyle(body).overflowY,footer:{top:f.top,bottom:f.bottom}};
    }""")
    assert dimensions['x']>=-1 and dimensions['y']>=-1,(kind,size,dimensions)
    assert dimensions['right']<=size['width']+1 and dimensions['bottom']<=size['height']+1,(kind,size,dimensions)
    assert dimensions['outer']<=dimensions['inner']+1,(kind,size,dimensions)
    assert dimensions['bodyHeight']>60 and dimensions['bodyScroll']=='auto',(kind,size,dimensions)
    assert dimensions['footer']['bottom']<=size['height']+1,(kind,size,dimensions)
    assert abs(dimensions['footer']['bottom']-dimensions['bottom'])<=2,(kind,size,dimensions)
    assert not page.evaluate("document.activeElement?.tagName==='INPUT'"),'Opening help must not automatically open the keyboard'
    for box in dialog.locator('.dialog-footer button,.dialog-close').all():
        r=box.bounding_box();assert r['height']>=43,(kind,size,r)
    return dimensions


def main():
    report=[]
    with sync_playwright() as p:
        browser=p.chromium.launch()
        for language in ('zh','en'):
            for dark in (False,True):
                for size in ({'width':360,'height':740},{'width':393,'height':851},{'width':844,'height':390},{'width':800,'height':1280},{'width':1280,'height':800}):
                    context=browser.new_context(viewport=size,user_agent='Android 14',is_mobile=True,has_touch=True)
                    context.route('https://api.kanokano.cn/**',lambda r:r.abort())
                    context.add_init_script(MOBILE+"\nlocalStorage.setItem('language',"+json.dumps(language)+");localStorage.setItem('theme',"+json.dumps('dark' if dark else 'light')+");")
                    page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                    page.goto('http://127.0.0.1:1420/index.html');page.wait_for_timeout(400)
                    for kind in ('guide','help','about','support'):
                        if kind=='guide':page.locator('.guide-callout button').click()
                        elif kind=='help':page.get_by_role('button',name='帮助' if language=='zh' else 'Help',exact=True).click()
                        else:page.get_by_role('button',name='关于与备份' if language=='zh' else 'About & backups',exact=True).click()
                        if kind=='support':
                            page.evaluate('window.__fixture.autoDismissSupport=false')
                            page.locator('.mobile-about-dialog .support-entry').click()
                        metrics=check(page,kind,size)
                        if kind=='guide':
                            for _ in range(2):
                                page.locator('.guide-controls button').last.click();check(page,kind,size)
                        if kind=='help':
                            page.locator('.help-topics button').last.click();check(page,kind,size)
                        if language=='zh' and dark and size['width']==393:
                            page.screenshot(path=str(ROOT/'verification'/('android-dialog-'+kind+'.png')))
                        page.locator('[role=dialog] .dialog-close').click();page.locator('[role=dialog]').wait_for(state='hidden')
                        report.append(dict(language=language,dark=dark,viewport=size,dialog=kind,passed=True))
                    assert not errors,errors
                    context.close()
        browser.close()
    (ROOT/'verification/android-dialog-regression.json').write_text(json.dumps(report,indent=2)+'\n')
    print('80 mobile dialog checks passed, including all guide steps, body scrolling, fixed actions and keyboard focus')


if __name__=='__main__':main()
