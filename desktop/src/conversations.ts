import { fetchSettingsAction } from './runtime';
import { VirtualList } from './virtual-list';
type Row=Record<string,unknown>;
import type { SettingsPage } from './settings-navigation';
type Page=SettingsPage;
const rows=(value:unknown):Row[]=>Array.isArray(value)?value.filter(v=>v&&typeof v==='object') as Row[]:[];
const element=(tag:string,className:string,text=''):HTMLElement=>{const e=document.createElement(tag);e.className=className;e.textContent=text;return e;};
const date=(value:unknown):string=>{const n=Number(value);return Number.isFinite(n)&&n>0?new Date(n*1000).toLocaleString('ko-KR',{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}):'';};
const phases:Record<string,string>={collecting:'대화를 가져오는 중',graphing:'기억을 정리하는 중',complete:'수집된 대화 정리 완료',pending:'다음 갱신에서 이어서 정리',paused:'갱신 일시 중지',failed:'갱신하지 못했습니다',interrupted:'이전 갱신 중단'};

export function wireConversationViews(load:typeof fetchSettingsAction=fetchSettingsAction):{select:(page:Page)=>void;visible:(flag:boolean)=>void;dispose:()=>void} {
  let page:Page='memory',dead=false,rooms:Row[]=[],sessions:Row[]=[],chatId='',sessionId='',chatEpoch=0,voiceEpoch=0;
  let chatItems:Row[]=[],voiceItems:Row[]=[],anchor:string|null=null,before:string|null=null,voiceBefore:number|null=null;
  let dbItems:Row[]=[],dbLoaded=false;
  let chatBusy=false,voiceBusy=false,dbBusy=false,dbBefore:number|null=null,timer:ReturnType<typeof setTimeout>|null=null;
  const listeners=new AbortController();const lists:VirtualList<Row>[]=[];
  let visible=true,viewEpoch=0;
  const get=(id:string)=>document.getElementById(id)!;
  const status=(id:string,text:string)=>{const node=get(id);if(node)node.textContent=text;};
  const bubble=(item:Row):HTMLElement=>{
    const row=element('article','message-row');row.dataset.self=String(item.is_self===true||item.role==='user');
    const body=element('div','message-bubble');
    const author=String(item.sender??'').trim()||(item.role==='assistant'?'올든':item.role==='user'||item.is_self===true?'나':item.type===0?'대화방':'대화 상대');
    body.append(element('div','message-author',author));
    const text=String(item.text??item.content??'');let notice=false;
    if(item.type===0){try{const feed=JSON.parse(text);notice=!!feed&&typeof feed==='object'&&!Array.isArray(feed)&&Number.isInteger(feed.feedType);}catch{/* Ordinary text remains unchanged. */}}
    if(notice){const original=element('details','message-notice');original.append(element('summary','','대화방 알림'),element('div','message-text',text));body.append(original);}
    else body.append(element('div','message-text',text));
    if(item.attachment){let note='첨부';try{const a=JSON.parse(String(item.attachment));note=String(a.name??a.filename??a.title??'첨부 메시지');}catch{note=String(item.attachment);}body.append(element('div','message-attachment',note));}
    body.append(element('time','message-date',date(item.sent_at??item.created_at)));row.append(body);return row;
  };
  const chatList=new VirtualList(get('chat-message-list'),r=>String(r.id),bubble,112);lists.push(chatList);
  const voiceList=new VirtualList(get('voice-message-list'),r=>String(r.turn_id)+':'+String(r.role),bubble,112);lists.push(voiceList);
  const chatRail=new VirtualList<Row>(get('chat-room-list'),r=>String(r.chat_id),r=>roomRow(r,false),70);lists.push(chatRail);
  const voiceRail=new VirtualList<Row>(get('voice-session-list'),r=>String(r.id),r=>roomRow(r,true),70);lists.push(voiceRail);
  const dbList=new VirtualList<Row>(get('db-cycle-list'),item=>String(item.id),item=>{ const row=element('article','db-cycle-row');row.setAttribute('role','listitem');row.append(element('time','',date(item.at)));const copy=element('div','');const details=item.details as Row??{};const accessWaiting=details.reason==='LocalDataAccessWaiting';copy.append(element('strong','',accessWaiting?'카카오톡 데이터 접근 확인 필요':String(item.phase)==='collecting'?'대화 수집':String(item.phase)==='graphing'?'기억 정리':phases[String(item.phase)]??'갱신 기록'));if(accessWaiting)copy.append(element('p','','macOS에서 올든의 다른 앱 데이터 접근 알림을 확인해 주세요.'));const counts=[];if(typeof details.rooms==='number')counts.push(`대화방 ${details.rooms}개`);if(typeof details.messages==='number')counts.push(`수집한 메시지 ${details.messages.toLocaleString('ko-KR')}개`);if(typeof details.indexed_messages==='number')counts.push(`검색 자료 ${details.indexed_messages.toLocaleString('ko-KR')}개`);if(typeof details.corpus_messages==='number'&&typeof details.corpus_pending==='number'&&details.corpus_pending>0)counts.push(`새 검색 자료 ${details.corpus_messages.toLocaleString('ko-KR')}개 · 남은 ${details.corpus_pending.toLocaleString('ko-KR')}개`);if(typeof details.nodes==='number')counts.push(`연결 항목 ${details.nodes}개`);copy.append(element('p','',counts.join(' · ')));row.append(copy,element('span','db-phase',String(item.phase)==='complete'?'완료':String(item.phase)==='failed'?'실패':String(item.phase)==='paused'?'중지':String(item.phase)==='pending'?'대기':String(item.phase)==='interrupted'?'중단':String(item.phase)==='collecting'?'수집':String(item.phase)==='graphing'?'정리':'기록'));return row; },84);lists.push(dbList);
  function placeholder(kind:'conversation'|'voice', heading:string, detail='', retry=false):void {
    const panel=get(kind+'-placeholder');panel.hidden=!heading;
    panel.querySelector('strong')!.textContent=heading;panel.querySelector('span')!.textContent=detail;
    get(kind+'-retry').hidden=!retry;
    get(kind==='voice'?'voice-message-list':'chat-message-list').hidden=!!heading;
  }
  function pickRoom(row:Row, voice:boolean):void {
    const id=String(voice?row.id:row.chat_id);
    if(voice){if(sessionId===id)return;sessionId=id;get('voice-start').dataset.conversationId=id;voiceEpoch++;voiceBefore=null;voiceItems=[];voiceList.set([]);status('voice-history-title',String(row.title??'음성 대화'));void readVoice(true);}
    else{if(chatId===id)return;chatId=id;chatEpoch++;before=anchor=null;chatItems=[];chatList.set([]);status('conversation-history-title',String(row.chat_name||'이름 없는 대화방'));void readChat(true);}
    filterRails();
  }
  function roomRow(row:Row,voice:boolean):HTMLElement {
    const id=String(voice?row.id:row.chat_id);const button=element('button','history-room') as HTMLButtonElement;button.type='button';button.setAttribute('aria-current',String(id===(voice?sessionId:chatId)));
    button.append(element('strong','',String(row.title??row.chat_name??'이름 없는 대화방')||'이름 없는 대화방'));
    button.append(element('span','',voice?date(row.started_at):date(row.last_updated_at)));
    button.onclick=()=>pickRoom(row,voice);return button;
  }
  function filterRails():void {
    const q=(get('conversation-search') as HTMLInputElement).value.trim().toLocaleLowerCase();chatRail.set(rooms.filter(r=>String(r.chat_name??'').toLocaleLowerCase().includes(q)),{preserve:true});
    const v=(get('voice-search') as HTMLInputElement).value.trim().toLocaleLowerCase();voiceRail.set(sessions.filter(r=>String(r.title??'').toLocaleLowerCase().includes(v)),{preserve:true});
  }
  async function roomData():Promise<void> {const epoch=viewEpoch;const data=await load('history-rooms');if(dead||!visible||epoch!==viewEpoch)return;if(data?.ok!==true){status('conversation-history-status','기록을 불러오지 못했습니다.');placeholder('conversation','기록을 불러오지 못했습니다','기억 정리에서 수집 상태를 확인하세요.',true);return;}rooms=rows(data.rooms);filterRails();if(!chatId&&rooms.length)pickRoom(rooms[0],false);else if(!rooms.length)placeholder('conversation','저장된 대화가 없습니다','기억 정리에서 수집 상태를 확인하세요.');}
  async function voiceData():Promise<void> {const epoch=viewEpoch;const data=await load('voice-history-sessions');if(dead||!visible||epoch!==viewEpoch)return;if(data?.ok!==true){status('voice-history-status','기록을 불러오지 못했습니다.');placeholder('voice','기록을 불러오지 못했습니다','다시 불러오거나 새 음성 대화를 시작하세요.',true);return;}sessions=rows(data.items);filterRails();if(!sessionId&&sessions.length)pickRoom(sessions[0],true);else if(!sessions.length)placeholder('voice','지난 음성 대화가 없습니다','음성으로 나눈 대화가 여기에 남습니다.');else if(sessionId&&voiceItems.length)void readVoice(false,true);}
  async function readChat(first=false):Promise<void> {
    if(!visible||page!=='conversation'||!chatId||chatBusy)return;chatBusy=true;const epoch=chatEpoch,id=chatId;status('conversation-history-status','불러오는 중');if(first)placeholder('conversation','대화를 불러옵니다');
    const query:Row={limit:100};if(anchor!==null)query.anchor=anchor;if(before!==null)query.before=before;
    try{const data=await load('history-messages',{chatId:id,query:JSON.stringify(query)});if(dead||epoch!==chatEpoch)return;
      if(data?.ok!==true){status('conversation-history-status','기록을 불러오지 못했습니다.');if(!chatItems.length)placeholder('conversation','기록을 불러오지 못했습니다','다시 불러오기를 누르세요.',true);return;}
      anchor=String(data.anchor_log_id);before=typeof data.next_before==='string'?data.next_before:null;
      const incoming=rows(data.messages);const known=new Set(chatItems.map(r=>r.id));chatItems=[...incoming.filter(r=>!known.has(r.id)),...chatItems];get('conversation-history-older').hidden=before===null;placeholder('conversation',chatItems.length?'':'이 대화의 기록이 없습니다');chatList.set(chatItems,{end:first,preserve:!first});
      status('conversation-history-status',`이 기기에 남은 ${Number(data.total).toLocaleString('ko-KR')}개 메시지 · ${chatItems.length.toLocaleString('ko-KR')}개 읽음`);
      get('conversation-history-older').hidden=before===null;
    }catch{if(!dead&&epoch===chatEpoch){status('conversation-history-status','기록을 불러오지 못했습니다.');if(!chatItems.length)placeholder('conversation','기록을 불러오지 못했습니다','다시 불러오기를 누르세요.',true);}}finally{chatBusy=false;if(epoch!==chatEpoch&&chatId&&!dead&&visible&&page==='conversation')void readChat(true);}
  }
  async function readVoice(first=false,refresh=false):Promise<void> {
    if(!visible||page!=='voice'||!sessionId||voiceBusy)return;voiceBusy=true;const epoch=voiceEpoch,id=sessionId;if(first)placeholder('voice','음성 대화를 불러옵니다');
    try{const data=await load('voice-history-messages',{chatId:id,query:JSON.stringify({limit:50,before:refresh?null:voiceBefore})});if(dead||epoch!==voiceEpoch)return;
      if(data?.ok!==true){status('voice-history-status','기록을 불러오지 못했습니다.');if(!voiceItems.length)placeholder('voice','기록을 불러오지 못했습니다','',true);return;}
      const incoming=rows(data.items);const key=(item:Row)=>String(item.turn_id)+':'+String(item.role);const oldKeys=new Set(voiceItems.map(key));const incomingKeys=new Set(incoming.map(key));if(!refresh||!incoming.some(item=>oldKeys.has(key(item))))voiceBefore=typeof data.next==='number'?data.next:null;voiceItems=refresh?[...voiceItems.filter(item=>!incomingKeys.has(key(item))),...incoming]:[...incoming.filter(item=>!oldKeys.has(key(item))),...voiceItems];get('voice-history-older').hidden=voiceBefore===null;placeholder('voice',voiceItems.length?'':'이 음성 대화의 기록이 없습니다');voiceList.set(voiceItems,{end:first||(refresh&&voiceList.followsLatest),preserve:!first});
      status('voice-history-status',`${voiceItems.length}개 말씀과 답변`);get('voice-history-older').hidden=voiceBefore===null;
    }catch{if(!dead&&epoch===voiceEpoch){status('voice-history-status','기록을 불러오지 못했습니다.');if(!voiceItems.length)placeholder('voice','기록을 불러오지 못했습니다','',true);}}finally{voiceBusy=false;if(epoch!==voiceEpoch&&sessionId&&!dead&&visible&&page==='voice')void readVoice(true);}
  }
  async function readDb(older=false):Promise<void> {
    if(!visible||page!=='history'||dbBusy)return;dbBusy=true;const epoch=viewEpoch;try{const data=await load('db-sync-history',{query:JSON.stringify({limit:50,before:older?dbBefore:null})});if(dead||!visible||epoch!==viewEpoch)return;
      if(data?.ok!==true){status('db-current-title','갱신 기록을 불러오지 못했습니다.');return;}
      const current=data.current as Row|undefined;const coverage=current?.details as Row|undefined;const coverageText=coverage&&typeof coverage.messages==='number'&&typeof coverage.indexed_messages==='number'?` · 수집 ${coverage.messages.toLocaleString('ko-KR')}개 · 검색 자료 ${coverage.indexed_messages.toLocaleString('ko-KR')}개`:'';status('db-current-title',current?phases[String(current.phase)]??'갱신 상태 확인 중':'첫 갱신을 기다립니다.');status('db-current-detail',current?date(current.updated_at)+coverageText:'대화를 수집하고 정리한 기록이 여기에 남습니다.');
      const incoming=rows(data.items);const knownIds=new Set(dbItems.map(item=>Number(item.id)));const missingMiddle=!older&&dbLoaded&&dbItems.length>0&&incoming.length>0&&!incoming.some(item=>knownIds.has(Number(item.id)));const merged=new Map(dbItems.map(item=>[Number(item.id),item]));for(const item of incoming)merged.set(Number(item.id),item);dbItems=[...merged.values()].sort((a,b)=>Number(b.id)-Number(a.id));
      dbList.set(dbItems,{preserve:true});
      if(older||!dbLoaded||missingMiddle)dbBefore=typeof data.next==='number'?data.next:null;dbLoaded=true;get('db-history-older').hidden=dbBefore===null;
    }catch{if(!dead&&epoch===viewEpoch)status('db-current-title','갱신 기록을 불러오지 못했습니다.');}finally{dbBusy=false;}
  }
  for(const id of ['conversation-search','voice-search'])get(id).addEventListener('input',filterRails,{signal:listeners.signal});
  get('conversation-retry').addEventListener('click',()=>{if(chatId)void readChat(true);else void roomData().catch(()=>placeholder('conversation','기록을 불러오지 못했습니다','기억 정리에서 수집 상태를 확인하세요.',true));},{signal:listeners.signal});
  get('voice-retry').addEventListener('click',()=>{if(sessionId)void readVoice(true);else void voiceData().catch(()=>placeholder('voice','기록을 불러오지 못했습니다','',true));},{signal:listeners.signal});
  get('conversation-history-older').addEventListener('click',()=>void readChat(),{signal:listeners.signal});get('voice-history-older').addEventListener('click',()=>void readVoice(),{signal:listeners.signal});get('db-history-older').addEventListener('click',()=>void readDb(true),{signal:listeners.signal});
  get('chat-message-list').addEventListener('scroll',()=>{if(visible&&page==='conversation'&&before!==null&&get('chat-message-list').scrollTop<70)void readChat();},{passive:true,signal:listeners.signal});
  get('voice-message-list').addEventListener('scroll',()=>{if(visible&&page==='voice'&&voiceBefore!==null&&get('voice-message-list').scrollTop<70)void readVoice();},{passive:true,signal:listeners.signal});
  function select(next:Page):void {if(page!==next){viewEpoch++;chatEpoch++;voiceEpoch++;}page=next;chatList.setVisible(visible&&page==='conversation');chatRail.setVisible(visible&&page==='conversation');voiceList.setVisible(visible&&page==='voice');voiceRail.setVisible(visible&&page==='voice');dbList.setVisible(visible&&page==='history');if(timer!==null)clearTimeout(timer);timer=null;if(!visible||dead)return;if(next==='conversation'){if(!rooms.length)void roomData().catch(()=>{if(!dead)placeholder('conversation','기록을 불러오지 못했습니다','기억 정리에서 수집 상태를 확인하세요.',true);});else if(chatId&&!chatItems.length)void readChat(true);}if(next==='voice'){void voiceData().catch(()=>{if(!dead)placeholder('voice','기록을 불러오지 못했습니다','',true);});if(sessionId&&!voiceItems.length)void readVoice(true);}if(next==='history'){void readDb();timer=setTimeout(()=>{if(!dead&&visible&&page==='history')select('history');},5000);}}
  return {select,visible:(flag)=>{visible=flag;if(!flag){viewEpoch++;chatEpoch++;voiceEpoch++;}select(page);},dispose:()=>{dead=true;chatEpoch++;voiceEpoch++;listeners.abort();if(timer!==null)clearTimeout(timer);lists.forEach(l=>l.dispose());}};
}
