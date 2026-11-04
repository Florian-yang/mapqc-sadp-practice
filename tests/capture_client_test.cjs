// DOM/device test doubles only; these checks do not use a physical camera/GPS.
const vm=require('node:vm'), fs=require('node:fs'), assert=require('node:assert/strict');
const code=fs.readFileSync('check_collect/static/capture.js','utf8');
function setup(){
 const elements={};
 for(const id of ['video','canvas','#open-camera','#take-photo','#upload','#notice','#preview','#capture-form'])
  elements[id]={handlers:{},disabled:true,addEventListener(k,f){this.handlers[k]=f;},removeAttribute(k){delete this[k];}};
 const tracks={stopped:false,stop(){this.stopped=true;}};
 elements.video.videoWidth=800;elements.video.videoHeight=600;elements.video.play=async()=>{};
 elements.canvas.getContext=()=>({drawImage(){}});elements.canvas.toDataURL=()=> 'data:image/jpeg;base64,U1lOVEhFVElD';
 const requests=[];let geoFail=false,resets=0;
 elements['#capture-form'].reset=()=>resets++;
 const context={console,Date,Math,Error,Promise,window:{isSecureContext:true,addEventListener(){}},document:{querySelector:k=>elements[k],addEventListener(){}},crypto:{randomUUID:()=> 'synthetic-capture-0001'},
  navigator:{mediaDevices:{getUserMedia:async options=>{assert.equal(options.audio,false);return {getTracks:()=>[tracks]};}},
   geolocation:{getCurrentPosition(ok,fail,options){assert.equal(options.maximumAge,0);if(geoFail)fail({message:'permission denied'});else ok({timestamp:Date.now(),coords:{latitude:30,longitude:114,accuracy:10}});}}},
  FormData:class{get(k){return {name:'synthetic',description:'unit test only',category:'shop'}[k];}},
  fetch:async(url,options)=>{requests.push({url,body:JSON.parse(options.body)});return {ok:true,json:async()=>({id:'synthetic-id'})};}};
 vm.runInNewContext(code,context);
 return {elements,requests,tracks,failGeo(){geoFail=true;},resets:()=>resets};
}
(async()=>{
 const a=setup();await a.elements['#open-camera'].handlers.click();await a.elements['#take-photo'].handlers.click();
 assert.equal(a.elements['#upload'].disabled,false);
 const event={preventDefault(){},currentTarget:a.elements['#capture-form']};
 const pending=a.elements['#capture-form'].handlers.submit(event);
 event.currentTarget=null; // Browser Event.currentTarget clears after dispatch.
 await pending;
 assert.equal(a.requests.length,1);assert.equal(a.requests[0].url,'/api/pois');
 assert.equal(a.requests[0].body.latitude,30);assert.equal(a.requests[0].body.longitude,114);
 assert.equal(a.requests[0].body.photo_base64,'U1lOVEhFVElD');
 assert.equal(a.resets(),1);assert.equal(a.tracks.stopped,true);assert.equal(a.elements['#upload'].disabled,true);
 const b=setup();await b.elements['#open-camera'].handlers.click();b.failGeo();await b.elements['#take-photo'].handlers.click();
 assert.equal(b.elements['#upload'].disabled,true);assert.equal(b.requests.length,0);
 const c=setup();await c.elements['#open-camera'].handlers.click();await c.elements['#take-photo'].handlers.click();
 c.failGeo();await c.elements['#take-photo'].handlers.click();assert.equal(c.elements['#upload'].disabled,true);
 console.log('3/3 client scenarios passed: upload/currentTarget, denied location, failed retake invalidates previous photo. Synthetic APIs only.');
})().catch(e=>{console.error(e);process.exitCode=1;});
