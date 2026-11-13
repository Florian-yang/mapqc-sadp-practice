'use strict';
(()=>{
 const badge=document.getElementById('notification-badge'),bell=document.getElementById('notification-bell');
 if(!badge||!bell)return;
 let busy=false;
 async function refresh(){
  if(busy||document.hidden)return;
  busy=true;
  try{
   const response=await fetch('/api/notifications/unread',{cache:'no-store'});
   if(!response.ok)return;
   const data=await response.json(),count=data.count;
   if(!Number.isSafeInteger(count)||count<0)return;
   badge.hidden=count===0;badge.textContent=count>99?'99+':String(count);
   bell.setAttribute('aria-label','我的通知，'+count+'条未读');
  }catch(_){/* Keep the last confirmed count when the network is unavailable. */}
  finally{busy=false;}
 }
 const timer=setInterval(refresh,15000);
 window.addEventListener('focus',refresh);
 document.addEventListener('visibilitychange',refresh);
 window.addEventListener('pagehide',()=>clearInterval(timer));
 refresh();
})();
