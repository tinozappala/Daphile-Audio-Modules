const {chromium} = require('C:/Users/Administrator/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
(async()=>{
 const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true,args:['--no-sandbox']});
 try{
  const page=await browser.newPage();const errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:8879/',{waitUntil:'domcontentloaded'});
  await page.locator('#studio').waitFor({state:'visible',timeout:20000});
  for(const [id,name,gain] of [['headphones-denon-ah-d9200','Denon AH-D9200','-7.2'],['headphones-hifiman-edition-xs','HiFiMAN Edition XS','-5.8']]){
   await page.selectOption('#presets',id);
   await page.waitForTimeout(700);
   if(!(await page.locator('#deletePreset').isDisabled()))throw Error('Deletion button enabled for '+name);
   if(await page.locator('#save').isDisabled())throw Error('Save button disabled for '+name);
   if((await page.locator('#bands tr').count())!==10)throw Error('Incorrect band count '+name);
   if((await page.locator('#pregain').inputValue())!==gain)throw Error('Incorrect pre-gain '+name);
   if((await page.locator('#presetName').inputValue())!==name)throw Error('Incorrect name '+name);
  }
  if(errors.length)throw Error(errors.join('; '));
  console.log('PASS: both profiles selectable, 10 editable bands, pre-gain present, delete disabled, save enabled.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
