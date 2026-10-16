document.querySelector('#legal-request-form').addEventListener('submit',async event=>{
  event.preventDefault();const form=event.currentTarget;const button=form.querySelector('button');const status=document.querySelector('#legal-request-status');button.disabled=true;
  try{const response=await fetch('/legal/requests',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(Object.fromEntries(new FormData(form))),cache:'no-store'});const result=await response.json();if(!response.ok)throw new Error(result.error||'Request failed');status.textContent=`Reference #${result.request_id}. ${result.message}`;form.reset();}catch(error){status.textContent=error.message;}finally{button.disabled=false;}
});
