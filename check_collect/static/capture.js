'use strict';
const video=document.querySelector('video'), canvas=document.querySelector('canvas');
const open=document.querySelector('#open-camera'), take=document.querySelector('#take-photo');
const send=document.querySelector('#upload'), note=document.querySelector('#notice');
const preview=document.querySelector('#preview');
let stream=null, draft=null, busy=false;
function invalidate(){draft=null;send.disabled=true;preview.removeAttribute('src');}
function stop(){if(stream){stream.getTracks().forEach(t=>t.stop());stream=null;}take.disabled=true;}
async function api(path,body){
  const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const value=await r.json();if(!r.ok)throw Error(value.error||'上传失败');return value;
}
open.addEventListener('click',async()=>{
  if(busy)return;invalidate();stop();
  if(!window.isSecureContext){note.textContent='拍照和定位需要HTTPS，请使用安全地址打开。';return;}
  busy=true;open.disabled=true;
  try{
    stream=await navigator.mediaDevices.getUserMedia({video:{facingMode:{ideal:'environment'}},audio:false});
    video.srcObject=stream;await video.play();take.disabled=false;note.textContent='相机已打开。对准POI，点击拍照并自动定位。';
  }catch(e){stop();note.textContent='无法打开相机，请检查设备与相机权限。';}
  finally{busy=false;open.disabled=false;}
});
take.addEventListener('click',async()=>{
  if(busy||!stream||!video.videoWidth)return;
  busy=true;take.disabled=true;open.disabled=true;invalidate();
  try{
    const scale=Math.min(1,1600/video.videoWidth);
    canvas.width=Math.round(video.videoWidth*scale);canvas.height=Math.round(video.videoHeight*scale);
    canvas.getContext('2d').drawImage(video,0,0,canvas.width,canvas.height);
    const captured_ms=Date.now(), image=canvas.toDataURL('image/jpeg',0.88);
    note.textContent='已拍照，正在获取设备位置…';
    const position=await new Promise((ok,fail)=>navigator.geolocation.getCurrentPosition(ok,fail,{enableHighAccuracy:true,maximumAge:0,timeout:15000}));
    if(Math.abs(position.timestamp-captured_ms)>20000)throw Error('位置时间与拍摄间隔过长，请重拍。');
    draft={capture_id:crypto.randomUUID(),photo_base64:image.split(',')[1],captured_ms,located_ms:position.timestamp,latitude:position.coords.latitude,longitude:position.coords.longitude,accuracy:position.coords.accuracy};
    preview.src=image;send.disabled=false;
    note.textContent=`自动定位：${draft.latitude.toFixed(6)}, ${draft.longitude.toFixed(6)}；精度约${Math.round(draft.accuracy)}米。请填写类别与描述后上传。`;
  }catch(e){invalidate();note.textContent=e.message||'未取得位置，请允许定位后重新拍照。';}
  finally{busy=false;take.disabled=!stream;open.disabled=false;}
});
document.querySelector('#capture-form').addEventListener('submit',async(event)=>{
  event.preventDefault();if(!draft||busy)return;
  const element=event.currentTarget, form=new FormData(element);
  busy=true;send.disabled=true;take.disabled=true;open.disabled=true;
  try{
    const context=element.dataset||{};
    const body={...draft,name:form.get('name'),category:form.get('category'),description:form.get('description')};
    if(context.poiId)Object.assign(body,{action:'recollect',issue_id:context.issueId,revision:Number(context.revision),reason:form.get('reason')});
    if(context.disputeId)Object.assign(body,{revision:Number(context.revision),decision:form.get('decision'),reason:form.get('reason')});
    const target=context.disputeId?`/api/disputes/${context.disputeId}/finalize`:context.poiId?`/api/review/${context.poiId}/respond`:'/api/pois';
    const result=await api(target,body);
    note.textContent='上传成功，记录编号：'+result.id;invalidate();element.reset();stop();
  }catch(e){note.textContent=e.message;send.disabled=!draft;}
  finally{busy=false;take.disabled=!stream;open.disabled=false;}
});
window.addEventListener('pagehide',()=>{stop();invalidate();});
document.addEventListener('visibilitychange',()=>{if(document.hidden&&!busy){stop();invalidate();}});
