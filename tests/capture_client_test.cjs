// DOM/device test doubles only; these checks do not use a physical camera/GPS.
const vm=require('node:vm'), fs=require('node:fs'), assert=require('node:assert/strict');
const code=fs.readFileSync('check_collect/static/capture.js','utf8');
function setup(recollect=false){
 const elements={};
 for(const id of ['video','canvas','#open-camera','#take-photo','#upload','#notice','#preview','#capture-form'])
  elements[id]={handlers:{},disabled:true,addEventListener(k,f){this.handlers[k]=f;},removeAttribute(k){delete this[k];}};
 const tracks={stopped:false,stop(){this.stopped=true;}};
 elements.video.videoWidth=800;elements.video.videoHeight=600;elements.video.play=async()=>{};
 elements.canvas.getContext=()=>({drawImage(){}});elements.canvas.toDataURL=()=> 'data:image/jpeg;base64,U1lOVEhFVElD';
 const requests=[];let geoFail=false,uploadFail=false,resets=0;
 elements['#capture-form'].reset=()=>resets++;
 if(recollect==='dispute')elements['#capture-form'].dataset={disputeId:'synthetic-dispute',revision:'2'};
 else if(recollect)elements['#capture-form'].dataset={poiId:'synthetic-poi',issueId:'synthetic-issue',revision:'2'};
 const context={console,Date,Math,Error,Promise,window:{isSecureContext:true,addEventListener(){}},document:{querySelector:k=>elements[k],addEventListener(){}},crypto:{randomUUID:()=> 'synthetic-capture-0001'},
  navigator:{mediaDevices:{getUserMedia:async options=>{assert.equal(options.audio,false);return {getTracks:()=>[tracks]};}},
   geolocation:{getCurrentPosition(ok,fail,options){assert.equal(options.maximumAge,0);if(geoFail)fail({message:'permission denied'});else ok({timestamp:Date.now(),coords:{latitude:30,longitude:114,accuracy:10}});}}},
  FormData:class{get(k){return {name:'synthetic',description:'unit test only',category:'shop',reason:'retaken after review',decision:'error_confirmed'}[k];}},
  fetch:async(url,options)=>{requests.push({url,body:JSON.parse(options.body)});return {ok:!uploadFail,json:async()=>uploadFail?{error:'记录已变化，请刷新'}:{id:'synthetic-id'}};}};
 vm.runInNewContext(code,context);
 return {elements,requests,tracks,failGeo(){geoFail=true;},failUpload(){uploadFail=true;},resets:()=>resets};
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
 const d=setup(true);await d.elements['#open-camera'].handlers.click();await d.elements['#take-photo'].handlers.click();
 await d.elements['#capture-form'].handlers.submit({preventDefault(){},currentTarget:d.elements['#capture-form']});
 assert.equal(d.requests[0].url,'/api/review/synthetic-poi/respond');
 assert.equal(d.requests[0].body.action,'recollect');assert.equal(d.requests[0].body.issue_id,'synthetic-issue');
 assert.equal(d.requests[0].body.revision,2);assert.equal(d.requests[0].body.reason,'retaken after review');
 const e=setup(true);await e.elements['#open-camera'].handlers.click();e.failGeo();await e.elements['#take-photo'].handlers.click();
 await e.elements['#capture-form'].handlers.submit({preventDefault(){},currentTarget:e.elements['#capture-form']});
 assert.equal(e.requests.length,0);
 const f=setup(true);await f.elements['#open-camera'].handlers.click();await f.elements['#take-photo'].handlers.click();f.failUpload();
 await f.elements['#capture-form'].handlers.submit({preventDefault(){},currentTarget:f.elements['#capture-form']});
 assert.equal(f.elements['#notice'].textContent,'记录已变化，请刷新');assert.equal(f.elements['#upload'].disabled,false);
 const g=setup('dispute');await g.elements['#open-camera'].handlers.click();await g.elements['#take-photo'].handlers.click();
 await g.elements['#capture-form'].handlers.submit({preventDefault(){},currentTarget:g.elements['#capture-form']});
 assert.equal(g.requests[0].url,'/api/disputes/synthetic-dispute/finalize');
 assert.equal(g.requests[0].body.decision,'error_confirmed');assert.equal(g.requests[0].body.revision,2);
 assert.equal(g.requests[0].body.reason,'retaken after review');assert.equal(g.requests[0].body.latitude,30);
 const h=setup('dispute');await h.elements['#open-camera'].handlers.click();h.failGeo();await h.elements['#take-photo'].handlers.click();
 await h.elements['#capture-form'].handlers.submit({preventDefault(){},currentTarget:h.elements['#capture-form']});assert.equal(h.requests.length,0);
 console.log('8/8 client scenarios passed: capture, recollection, actionable failure, third-party payload and denied third-party location. Synthetic APIs only.');
})().catch(e=>{console.error(e);process.exitCode=1;});
