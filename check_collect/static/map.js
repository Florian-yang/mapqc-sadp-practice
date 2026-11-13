'use strict';
const mapNode=document.getElementById('poi-map'), notice=document.getElementById('map-notice');
const filters=document.getElementById('map-filter'), summary=document.getElementById('route-summary');
const stepsNode=document.getElementById('route-steps'), ocrNode=document.getElementById('ocr-result');
const stopButton=document.getElementById('stop-navigation');
const listNode=document.getElementById('poi-list'),detailNode=document.getElementById('poi-detail');
const categories={house:'房屋',shop:'商铺',bus_stop:'公交站',greenery:'绿化',scenic:'景点',other:'其他'};
const statuses={pending:'待核验',needs_action:'待采集者处理',disputed:'采集者已驳回',under_arbitration:'第三方处理中',finalized:'第三方已裁决'};
const modifiers={left:'左转',right:'右转','slight left':'稍向左','slight right':'稍向右','sharp left':'向左急转','sharp right':'向右急转',straight:'直行',uturn:'掉头'};
let map=null,featuresLayer=null,routeLayer=null,positionLayer=null,watchId=null,loadVersion=0,routeVersion=0,ocrBusy=false;
function element(tag,text){const node=document.createElement(tag);if(text!==undefined)node.textContent=text;return node;}
async function request(url,body){
  const response=await fetch(url,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const value=await response.json();if(!response.ok)throw Error(value.error||'请求失败');return value;
}
function stopFollowing(){if(watchId!==null)navigator.geolocation.clearWatch(watchId);watchId=null;stopButton.disabled=true;}
function updatePosition(position){
  const p=[position.coords.latitude,position.coords.longitude];
  if(!positionLayer)positionLayer=L.circleMarker(p,{radius:7,color:'#1d4ed8',fillOpacity:1}).addTo(map);
  else positionLayer.setLatLng(p);
}
function instruction(step){
  if(step.type==='depart')return '出发'+(step.name?'，沿'+step.name:'');
  if(step.type==='arrive')return '到达目的地';
  const turn=modifiers[step.modifier]||'沿路线行驶';
  return turn+(step.name?'，进入'+step.name:'')+'，约'+Math.round(step.distance)+'米';
}
async function navigate(feature){
  const ticket=++routeVersion;stopFollowing();summary.replaceChildren();stepsNode.replaceChildren();
  if(routeLayer){map.removeLayer(routeLayer);routeLayer=null;}
  if(!window.isSecureContext){notice.textContent='导航定位需要HTTPS。';return;}
  notice.textContent='正在获取设备位置…';
  try{
    const position=await new Promise((ok,fail)=>navigator.geolocation.getCurrentPosition(ok,fail,{enableHighAccuracy:true,maximumAge:0,timeout:15000}));
    if(ticket!==routeVersion)return;
    const result=await request('/api/maps/route',{poi_id:feature.properties.id,latitude:position.coords.latitude,longitude:position.coords.longitude});
    if(ticket!==routeVersion)return;
    updatePosition(position);
    routeLayer=L.geoJSON(result.geometry,{style:{color:'#1d4ed8',weight:5}}).addTo(map);
    map.fitBounds(routeLayer.getBounds(),{padding:[20,20]});
    summary.append(element('p',result.destination.name+'：'+(result.distance/1000).toFixed(2)+'公里，预计'+Math.ceil(result.duration/60)+'分钟（驾车）'));
    const external=element('a','在OpenStreetMap中打开路线');external.href=result.navigation_url;external.target='_blank';external.rel='noopener noreferrer';summary.append(external);
    result.steps.forEach(step=>stepsNode.append(element('li',instruction(step))));
    watchId=navigator.geolocation.watchPosition(updatePosition,()=>{stopFollowing();notice.textContent='位置跟随中断，请检查定位权限。';},{enableHighAccuracy:true,maximumAge:5000,timeout:15000});
    stopButton.disabled=false;notice.textContent='路线已载入。位置持续更新；偏离路线时可重新点击导航。';
  }catch(error){if(ticket===routeVersion)notice.textContent=error.message||'未取得定位或路线。';}
}
async function recognize(feature){
  if(ocrBusy)return;
  ocrBusy=true;
  ocrNode.replaceChildren(element('p','正在识别照片文字…'));
  try{
    const result=await request('/api/pois/'+feature.properties.id+'/ocr',{language:'auto',expected_revision:feature.properties.revision,confirmed_external:true});
    ocrNode.replaceChildren(element('h3',feature.properties.name+'：识别结果'),element('pre',result.text||'未识别到文字'));
    if(result.partial)ocrNode.append(element('p','部分内容识别成功，请核对照片。'));
    if(result.suggested_category)ocrNode.append(element('p','建议类别：'+categories[result.suggested_category]+'，由采集者核对后更新。'));
  }catch(error){ocrNode.replaceChildren(element('p',error.message));}
  finally{ocrBusy=false;}
}
function popup(feature){
  const p=feature.properties,box=element('div');
  box.append(element('strong',p.name),element('p',categories[p.category]+' · '+statuses[p.status]+(p.has_error?' · 有待处理错误':'')),element('p',p.description));
  const photo=element('img');photo.src='/api/pois/'+p.id+'/photo?revision='+p.revision;photo.alt='当前条目版本的采集照片';photo.style.maxWidth='220px';box.append(photo,element('br'));
  const details=element('a','查看POI记录');details.href='/poi/'+p.id;box.append(details,element('br'));
  const nav=element('button','导航至此POI');nav.type='button';nav.addEventListener('click',()=>navigate(feature));box.append(nav,element('br'));
  const consent=element('input');consent.type='checkbox';
  const label=element('label');label.append(consent,element('span','同意将这张照片发送至百度OCR'));
  const ocr=element('button','识别照片文字');ocr.type='button';ocr.disabled=true;
  consent.addEventListener('change',()=>{ocr.disabled=!consent.checked||mapNode.dataset.ocrReady!=='true';});
  ocr.addEventListener('click',()=>{if(consent.checked)return recognize(feature);});box.append(label,element('br'),ocr);
  return box;
}
function showDetail(feature){
  if(!detailNode)return;
  detailNode.replaceChildren(popup(feature));
  ocrNode.replaceChildren();
  if(listNode)for(const row of listNode.children)row.className='poi-row'+(row.dataset.poiId===feature.properties.id?' selected':'');
}
function showList(features){
  if(!listNode)return;
  listNode.replaceChildren();
  document.getElementById('poi-count').textContent=features.length;
  document.getElementById('error-count').textContent=features.filter(f=>f.properties.has_error).length;
  for(const feature of features){
    const p=feature.properties,row=element('button');row.type='button';row.className='poi-row';row.dataset.poiId=p.id;
    row.append(element('strong',p.name),element('span',categories[p.category]+' · '+statuses[p.status]));
    row.addEventListener('click',()=>{showDetail(feature);map.setView([feature.geometry.coordinates[1],feature.geometry.coordinates[0]],17);});
    listNode.append(row);
  }
  if(features.length)showDetail(features[0]);
  else{
    listNode.append(element('p','没有符合筛选条件的点位。'));
    detailNode.replaceChildren(element('h2','暂无点位'),element('p','可调整筛选条件，或使用手机采集新的POI。'));
    ocrNode.replaceChildren();
  }
}
async function load(){
  const ticket=++loadVersion,form=new FormData(filters),query=new URLSearchParams();
  for(const key of ['category','status'])if(form.get(key))query.set(key,form.get(key));
  notice.textContent='正在查询POI…';
  try{
    const result=await request('/api/map?'+query.toString());if(ticket!==loadVersion)return;
    if(featuresLayer)map.removeLayer(featuresLayer);
    featuresLayer=L.geoJSON(result,{pointToLayer:(feature,latlng)=>L.circleMarker(latlng,{radius:9,color:feature.properties.status==='under_arbitration'?'#b45309':feature.properties.has_error?'#b91c1c':feature.properties.status==='finalized'?'#15803d':'#1d4ed8',fillOpacity:.8}),onEachFeature:(feature,layer)=>{layer.bindPopup(popup(feature));if(detailNode)layer.on('click',()=>showDetail(feature));}}).addTo(map);
    showList(result.features);
    if(result.features.length)map.fitBounds(featuresLayer.getBounds(),{padding:[25,25],maxZoom:16});
    notice.textContent='显示'+result.features.length+'个POI。';
  }catch(error){if(ticket===loadVersion)notice.textContent=error.message;}
}
stopButton.addEventListener('click',()=>{stopFollowing();notice.textContent='已停止位置跟随。';});
filters.addEventListener('submit',event=>{event.preventDefault();load();});
window.addEventListener('pagehide',()=>{++routeVersion;stopFollowing();});
if(typeof L==='undefined'){notice.textContent='地图组件加载失败，请刷新页面。';}
else{
  map=L.map(mapNode).setView([30.54,114.36],13);
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'}).on('tileerror',()=>{notice.textContent='底图暂时无法加载，POI及路线查询可稍后重试。';}).addTo(map);
  load();
}
