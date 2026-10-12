const $ = id => document.getElementById(id);
let currentWeather = null, mediaRecorder = null, audioChunks = [], silenceTimer = null, audioContext = null, analyser = null, micStream = null, recording = false, isSubmitting = false;
const icons = {"01":"☀","02":"🌤","03":"☁","04":"☁","09":"🌧","10":"🌦","11":"⛈","13":"❄","50":"🌫"};
function iconFor(icon){return icons[(icon||"01").slice(0,2)]||"☀"}
function toast(msg){const t=$("toast");t.textContent=msg;t.classList.add("show");clearTimeout(toast.timer);toast.timer=setTimeout(()=>t.classList.remove("show"),3400)}
function localTime(ts, offset){if(!ts)return "—";return new Intl.DateTimeFormat(undefined,{hour:"2-digit",minute:"2-digit",timeZone:"UTC"}).format(new Date((ts+(offset||0))*1000))}
function localDate(ts, offset){return new Intl.DateTimeFormat(undefined,{weekday:"long",month:"long",day:"numeric",timeZone:"UTC"}).format(new Date((ts+(offset||0))*1000))}
function setPhase(phase, clouds=0, condition="Clear"){
  const sky=$("sky");sky.classList.remove("sky-day","sky-dawn","sky-sunset","sky-night");
  const lower=(condition||"").toLowerCase();
  if(phase==="night")sky.classList.add("sky-night");else if(phase==="dawn")sky.classList.add("sky-dawn");else if(phase==="sunset")sky.classList.add("sky-sunset");else sky.classList.add("sky-day");
  // Weather influences brightness, while animated clouds remain independent and drift over the interface.
  sky.style.filter = (lower.includes("rain")||lower.includes("storm")||lower.includes("snow")||clouds>80) ? "brightness(.77) saturate(.78)" : clouds>45 ? "brightness(.9) saturate(.9)" : "none";
  $("phaseChip").textContent=({night:"☾ NIGHT SKY",dawn:"☼ DAWN GLOW",sunset:"◒ GOLDEN HOUR",day:"✦ DAYLIGHT"})[phase]||"✦ LIVE SKY";
}
async function api(path, body){const r=await fetch(path,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});const data=await r.json().catch(()=>({}));if(!r.ok)throw new Error(data.detail||"Request failed");return data}
async function searchWeather(query){query=(query||"").trim();if(!query)return; $("searchBtn").disabled=true;$("searchBtn").textContent="Loading…";try{const data=await api("/api/weather",{query});renderWeather(data);$("searchInput").value=query;await loadHistory()}catch(e){toast(e.message)}finally{$("searchBtn").disabled=false;$("searchBtn").innerHTML='Explore <span>↗</span>'}}
async function coordsWeather(lat,lon){try{const data=await api("/api/weather/coords",{lat,lon});renderWeather(data);await loadHistory()}catch(e){toast(e.message)}}
function renderWeather(w){
  currentWeather=w;const condition=(w.condition||"").toLowerCase();$("place").textContent=w.place;$("conditionLabel").textContent="YOUR SKY, RIGHT NOW";$("dateLine").textContent=localDate(w.dt,w.timezone_offset)+" · Local time "+localTime(w.dt,w.timezone_offset);
  $("temperature").innerHTML=`${w.temp}<span>°</span>`;$("condition").textContent=w.description;$("feels").textContent=`Feels like ${w.feels_like}° · H ${w.temp_max}° / L ${w.temp_min}°`;$("weatherIcon").textContent=iconFor(w.icon);
  $("statFeels").textContent=`${w.feels_like}°`;$("statHumidity").textContent=`${w.humidity}%`;$("statWind").textContent=`${Math.round((w.wind_speed||0)*3.6)} km/h`;$("statClouds").textContent=`${w.clouds}%`;
  $("coordinates").textContent=`LAT ${Number(w.lat).toFixed(3)} · LON ${Number(w.lon).toFixed(3)}`;$("statPressure").textContent=`${w.pressure} hPa`;$("statVisibility").textContent=w.visibility==null?"—":`${(w.visibility/1000).toFixed(1)} km`;$("statSunrise").textContent=localTime(w.sunrise,w.timezone_offset);$("statSunset").textContent=localTime(w.sunset,w.timezone_offset);
  setPhase(w.phase,w.clouds,w.condition);
  $("hourly").innerHTML=(w.hourly||[]).map((h,i)=>`<div class="hour"><div class="hour-time">${i===0?"Now":localTime(h.dt,w.timezone_offset)}</div><div class="hour-icon">${iconFor(h.icon)}</div><div class="hour-temp">${h.temp}°</div><div class="hour-desc">${h.description||""}</div></div>`).join("")||'<div class="empty-forecast">No forecast available.</div>';
  $("generatedScene").classList.remove("show");$("weatherCard").classList.remove("has-scene");$("card-shade").style.backgroundImage="";$("transcript").textContent=`Showing current conditions for ${w.place}.`;
}
$("searchBtn").addEventListener("click",()=>searchWeather($("searchInput").value));
$("searchInput").addEventListener("keydown",e=>{if(e.key==="Enter")searchWeather($("searchInput").value)});
document.querySelectorAll(".suggest").forEach(b=>b.addEventListener("click",()=>searchWeather(b.dataset.query)));
$("locateBtn").addEventListener("click",()=>{if(!navigator.geolocation){toast("Geolocation is not supported by this browser.");return}navigator.geolocation.getCurrentPosition(p=>coordsWeather(p.coords.latitude,p.coords.longitude),e=>toast(e.message||"Location permission was not granted."),{enableHighAccuracy:true,timeout:12000})});
$("sceneBtn").addEventListener("click",async()=>{if(!currentWeather){toast("Search for a place first.");return}const btn=$("sceneBtn");btn.disabled=true;btn.textContent="✧ Creating scene…";try{const r=await api("/api/scene",{lat:currentWeather.lat,lon:currentWeather.lon});$("generatedScene").style.backgroundImage=`url("${r.image}")`;$("generatedScene").classList.add("show");$("card-shade").style.backgroundImage=`url("${r.image}")`;$("weatherCard").classList.add("has-scene");toast("Your local sky scene is ready.")}catch(e){toast(e.message)}finally{btn.disabled=false;btn.textContent="✧ Generate local scene"}});
async function loadHistory(){try{const r=await fetch("/api/history");const data=await r.json();const items=data.items||[];$("historyList").innerHTML=items.length?items.map(x=>`<button class="history-item" data-lat="${x.lat}" data-lon="${x.lon}"><span>⌖ ${escapeHtml(x.place||x.query||"Unknown place")}<small>${x.lat.toFixed(2)}, ${x.lon.toFixed(2)}</small></span><span>↗</span></button>`).join(""):'<div class="history-empty">No saved searches yet.</div>';$("historyList").querySelectorAll(".history-item").forEach(b=>b.addEventListener("click",()=>coordsWeather(Number(b.dataset.lat),Number(b.dataset.lon))))}catch{}}
function escapeHtml(s){return String(s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]))}
$("refreshHistory").addEventListener("click",loadHistory);
async function beginRecording(){
 if(!navigator.mediaDevices?.getUserMedia||!window.MediaRecorder){toast("Voice recording needs a modern browser and HTTPS.");return}
 try{
  micStream=await navigator.mediaDevices.getUserMedia({audio:true});audioChunks=[];mediaRecorder=new MediaRecorder(micStream);recording=true;$("orbButton").classList.add("listening");$("orbStatus").textContent="Listening… tap again to send";$("talkBtn").textContent="■ Stop & send";$("transcript").textContent="Listening. Speak naturally…";
  mediaRecorder.ondataavailable=e=>{if(e.data.size)audioChunks.push(e.data)};
  mediaRecorder.onstop=async()=>{recording=false;clearTimeout(silenceTimer);if(micStream)micStream.getTracks().forEach(t=>t.stop());$("orbButton").classList.remove("listening");$("orbStatus").textContent="Thinking…";$("talkBtn").textContent="◉ Talk to Sphere";if(audioChunks.length)await processAudio(new Blob(audioChunks,{type:mediaRecorder.mimeType||"audio/webm"}));else{$("orbStatus").textContent="Tap the sphere to speak"}};
  mediaRecorder.start(250);startSilenceDetection(micStream);
 }catch(e){toast("Microphone permission is needed to speak to Sphere.");}
}
function startSilenceDetection(stream){
 try{audioContext=new (window.AudioContext||window.webkitAudioContext)();const source=audioContext.createMediaStreamSource(stream);analyser=audioContext.createAnalyser();analyser.fftSize=512;source.connect(analyser);const buffer=new Uint8Array(analyser.fftSize);let spoke=false,lastSound=Date.now();
  const tick=()=>{if(!recording)return;analyser.getByteTimeDomainData(buffer);let sum=0;for(const n of buffer){const v=(n-128)/128;sum+=v*v}const rms=Math.sqrt(sum/buffer.length);if(rms>.025){spoke=true;lastSound=Date.now();$("orbButton").style.setProperty("--volume",Math.min(1,rms*8))}if(spoke&&Date.now()-lastSound>1800){stopRecording();return}requestAnimationFrame(tick)};tick();
 }catch{}
}
function stopRecording(){if(recording&&mediaRecorder&&mediaRecorder.state!=="inactive")mediaRecorder.stop();if(audioContext){audioContext.close().catch(()=>{});audioContext=null}}
$("orbButton").addEventListener("click",()=>recording?stopRecording():beginRecording());
$("talkBtn").addEventListener("click",()=>recording?stopRecording():beginRecording());
async function processAudio(blob){
 if(isSubmitting)return;isSubmitting=true;
 try{
  $("orbStatus").textContent="Transcribing voice…";const form=new FormData();form.append("file",blob,"weather-question.webm");const r=await fetch("/api/transcribe",{method:"POST",body:form});const tr=await r.json();if(!r.ok)throw new Error(tr.detail||"Could not transcribe audio");const text=(tr.text||"").trim();if(!text)throw new Error("I didn't catch that. Please try again.");$("transcript").textContent=`“${text}”`;$("orbStatus").textContent="Finding your weather…";
  const result=await api("/api/assistant",{query:text});if(result.weather){renderWeather(result.weather);$("orbStatus").textContent="Here's your weather";await loadHistory()}else{$("orbStatus").textContent=result.reply||"Could you repeat the location?";$("transcript").textContent=result.reply||"Please repeat the location."}
  if(result.reply)await speak(result.reply);else if(result.weather)await speak(`The weather in ${result.weather.place} is ${result.weather.description}, ${result.weather.temp} degrees.`);
 }catch(e){$("orbStatus").textContent="Tap the sphere to try again";$("transcript").textContent=e.message;toast(e.message)}finally{isSubmitting=false;setTimeout(()=>$("orbStatus").textContent="Tap the sphere to speak",4500)}
}
async function speak(text){try{const r=await fetch("/api/speak",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({query:text})});if(!r.ok)return;const blob=await r.blob();const url=URL.createObjectURL(blob);const audio=new Audio(url);audio.play().catch(()=>{});audio.onended=()=>URL.revokeObjectURL(url)}catch{}}
$("clock").textContent=new Intl.DateTimeFormat(undefined,{weekday:"short",month:"short",day:"numeric"}).format(new Date()).toUpperCase()+" · LIVE CONDITIONS";
loadHistory();
  
