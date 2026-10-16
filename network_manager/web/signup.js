(() => {
  'use strict';
  const form=document.querySelector('#signup-form');
  const status=document.querySelector('#signup-status');
  const submit=document.querySelector('#signup-submit');
  const retry=document.querySelector('#signup-retry');
  let policyVersion=null;
  let busy=false;
  function message(text,error=false,focus=false){status.textContent=text;status.classList.toggle('error',error);if(focus){status.focus();status.scrollIntoView({block:'center',behavior:'smooth'});}}
  async function request(path,options={}){
    const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),20000);
    try{
      const response=await fetch(path,{...options,cache:'no-store',signal:controller.signal});
      let result;try{result=await response.json();}catch{throw new Error('The server returned an unexpected response. Reload and try again.');}
      if(!response.ok)throw new Error(result.error||`Request failed (${response.status}).`);
      return result;
    }catch(error){if(error.name==='AbortError')throw new Error('The request timed out and may already have been received. Check with your administrator before submitting again.');throw error;}
    finally{clearTimeout(timer);}
  }
  async function loadSettings(){
    submit.disabled=true;retry.hidden=true;policyVersion=null;message('Checking registration availability…');
    try{const settings=await request('/auth/config');if(!settings.registration_open){message('Registration is closed. Contact your platform administrator for access.',true);retry.hidden=false;return;}if(!settings.policy_version)throw new Error('Registration settings are incomplete. Contact your platform administrator.');policyVersion=settings.policy_version;submit.disabled=false;message('Complete all fields and accept the terms, then select Create account request.');}
    catch(error){message(error.message,true);retry.hidden=false;}
  }
  retry.addEventListener('click',loadSettings);
  document.querySelector('#signup-show-password').addEventListener('click',event=>{
    const show=form.elements.password.type==='password';form.elements.password.type=show?'text':'password';form.elements.confirm_password.type=show?'text':'password';event.currentTarget.textContent=show?'Hide passwords':'Show passwords';event.currentTarget.setAttribute('aria-pressed',String(show));
  });
  form.addEventListener('submit',async event=>{
    event.preventDefault();if(busy)return;
    if(!form.checkValidity()){message('Please complete the required fields, use a valid email and a password of at least 12 characters, and accept the terms.',true,true);form.reportValidity();return;}
    if(form.elements.password.value!==form.elements.confirm_password.value){message('Passwords do not match. Enter the same password twice.',true,true);form.elements.confirm_password.focus();return;}
    if(!policyVersion){message('Registration settings are not ready. Select Reload registration settings.',true,true);retry.hidden=false;return;}
    const values=Object.fromEntries(new FormData(form));values.terms_accepted=form.elements.terms_accepted.checked;values.policy_version=policyVersion;delete values.confirm_password;
    values.name=values.name.trim();values.tenant_name=values.tenant_name.trim();values.email=values.email.trim();
    busy=true;submit.disabled=true;submit.textContent='Submitting…';message('Creating your account request…',false,true);
    try{const result=await request('/auth/register',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(values)});form.reset();form.hidden=true;const success=document.querySelector('#signup-success');document.querySelector('#signup-result').textContent=result.message;success.hidden=false;success.setAttribute('tabindex','-1');success.focus();success.scrollIntoView({block:'center',behavior:'smooth'});}
    catch(error){message(error.message,true,true);}
    finally{busy=false;submit.disabled=false;submit.textContent='Create account request';}
  });
  loadSettings();
})();
