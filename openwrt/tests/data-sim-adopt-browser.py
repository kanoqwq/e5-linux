"""Real data-card control permits adopting the live card; all RPC is mocked."""
from pathlib import Path
from playwright.sync_api import sync_playwright

TOP = Path(__file__).resolve().parents[2]
SOURCE = (TOP / 'openwrt/luci-app-t551m/htdocs/luci-static/resources/mu300/data-sim.js').read_text()

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe')
    for width in (390, 1280):
        page = browser.new_page(viewport={'width':width, 'height':720})
        page.route('http://data-card.test/**', lambda route: route.fulfill(content_type='text/html', body='<main id="root"></main>'))
        page.goto('http://data-card.test/')
        for actual, saved in ((1,0), (0,1), (1,1), (0,0)):
            page.evaluate('''([source,actual,saved]) => {
                window.control?.dispose(); sessionStorage.clear();
                const root=document.querySelector('#root'); root.innerHTML='';
                window.calls=[]; window.confirmed=true;
                window.info={data_sim:actual,saved_sim:saved,data_up:false};
                const M={localize:()=>{},translate:s=>s,busy:()=>{},
                    followDataSim:(slot)=>calls.push(['follow',slot]),
                    confirmBox:()=>Promise.resolve(confirmed),
                    callSimSet:(target,current,confirm)=>{
                        calls.push(['set',target,current,confirm]);
                        window.info={data_sim:Number(target),saved_sim:Number(target),data_up:true};
                        return Promise.resolve({ok:true,state:'done',target});
                    },
                    callSimGet:()=>Promise.resolve(window.info)};
                const module=new Function('baseclass','M',source)({extend:x=>x},M);
                window.control=module.attach(root); control.update(info);
            }''', [SOURCE,actual,saved])
            button = page.locator('button')
            assert button.is_disabled() == (actual == saved), (width,actual,saved)
            if actual != saved:
                page.evaluate('window.confirmed=false')
                button.click()
                page.wait_for_function('!document.querySelector("button").disabled')
                assert page.evaluate('calls') == []
                page.evaluate('window.confirmed=true')
                button.click()
                page.wait_for_function('calls.length===2 && document.querySelector("button").disabled')
                assert page.evaluate('calls') == [['set',str(actual),str(actual),True],['follow',str(actual)]]
            else:
                page.evaluate('document.querySelector("button").onclick()')
                assert page.evaluate('calls') == []
        page.close()
    browser.close()
print('PASS E5 data-card adoption: both directions, matching-card no-op, cancel, follow and desktop/mobile controls; mocked RPC only')
