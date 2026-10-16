const {JSDOM}=require('jsdom');
const fs=require('fs');
const assert=require('node:assert/strict');
const root=process.argv[2]||require('path').resolve(__dirname,'..');
const wait=()=>new Promise(r=>setTimeout(r,0));
async function make(postResponse={ok:true,body:{message:'Registration received. Approval required.'}}){
 const dom=new JSDOM(fs.readFileSync(root+'/web/signup.html','utf8'),{url:'https://netwizzard.test/signup',runScripts:'outside-only'});
 const w=dom.window;w.HTMLElement.prototype.scrollIntoView=function(){};
 const calls=[];w.fetch=async(url,opts={})=>{if(url==='/auth/config')return {ok:true,json:async()=>({registration_open:true,policy_version:'2026-10-16'})};calls.push({url,opts});await wait();return {ok:postResponse.ok,json:async()=>postResponse.body};};
 w.eval(fs.readFileSync(root+'/web/signup.js','utf8'));await wait();
 return {dom,w,form:w.document.querySelector('#signup-form'),calls};
}
function fill(form){for(const [k,v]of Object.entries({name:'Sample User',tenant_name:'Sample Workspace',email:'sample@example.com',password:'a-long-test-password',confirm_password:'a-long-test-password'}))form.elements[k].value=v;form.elements.terms_accepted.checked=true;}
function send(t){t.form.dispatchEvent(new t.w.Event('submit',{bubbles:true,cancelable:true}));}
(async()=>{
 let t=await make();assert.equal(t.w.document.querySelector('#signup-submit').disabled,false);fill(t.form);send(t);send(t);await wait();await wait();assert.equal(t.calls.length,1);const body=JSON.parse(t.calls[0].opts.body);assert.equal(body.terms_accepted,true);assert.equal(body.policy_version,'2026-10-16');assert.equal('confirm_password'in body,false);assert.equal(t.w.document.querySelector('#signup-success').hidden,false);assert.equal(t.form.hidden,true);t.dom.window.close();
 t=await make();fill(t.form);t.form.elements.confirm_password.value='different-password';send(t);assert.equal(t.calls.length,0);assert.match(t.w.document.querySelector('#signup-status').textContent,/do not match/);t.dom.window.close();
 t=await make();fill(t.form);t.form.elements.terms_accepted.checked=false;send(t);assert.equal(t.calls.length,0);assert.match(t.w.document.querySelector('#signup-status').textContent,/accept the terms/);t.dom.window.close();
 t=await make({ok:false,body:{error:'Too many attempts. Try again later.'}});fill(t.form);send(t);await wait();await wait();assert.match(t.w.document.querySelector('#signup-status').textContent,/Too many attempts/);assert.equal(t.w.document.querySelector('#signup-submit').disabled,false);assert.equal(t.form.hidden,false);t.dom.window.close();
 const page=new JSDOM(fs.readFileSync(root+'/web/index.html','utf8'),{url:'https://netwizzard.test/',runScripts:'outside-only'});page.window.eval(fs.readFileSync(root+'/web/app.js','utf8'));assert.equal(page.window.document.querySelector('#signup-link').getAttribute('href'),'/signup');page.window.close();
 console.log('PASS: signup link and main JS initialization; successful request/confirmation; double-click prevention; password mismatch; required agreement; visible server error with retry.');
})().catch(e=>{console.error(e);process.exit(1)});
