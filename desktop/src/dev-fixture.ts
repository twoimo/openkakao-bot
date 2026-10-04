/** Explicit development-only synthetic data. Never included in native boot. */
import { unavailableSnapshot, type EmergencyState } from './contracts';
import type { fetchSettingsAction, SettingsInvoke } from './runtime';
import { RESIDENT_MODEL_ID, SWAP_MODEL_ID } from './tokens';
const at=Date.now()/1000;
const rooms=[{chat_id:'101',chat_name:'예시 · 디자인 이야기',last_updated_at:at},{chat_id:'202',chat_name:'예시 · 책을 읽는 사람들',last_updated_at:at-3600}];
let catalog=[{chat_id:101,title:rooms[0].chat_name,auto_reply:true,geeknews:false}];
export let emergency:EmergencyState={schemaVersion:1,epoch:0,latched:false,reason:'initial'};
let selectedModel: string = RESIDENT_MODEL_ID;
export const invokeCommand: SettingsInvoke = async <T>(command: string, args: Record<string,unknown> = {}): Promise<T> => {
  if(command==='fetch_settings_action'&&args.action==='model-set'&&(args.model===RESIDENT_MODEL_ID||args.model===SWAP_MODEL_ID)) {
    selectedModel=args.model;return {ok:true,action:'model-set',model:selectedModel,stored:true,prepared:true,needs_prepare:false} as T;
  }
  throw new Error('development_fixture_command_unavailable');
};
const nodes=[{id:'root',label:'카카오톡',category:'collection',importance:100},{id:'people',label:'인물',category:'collection',importance:98},{id:'rooms',label:'대화방',category:'collection',importance:98},{id:'topics',label:'주제',category:'collection',importance:98},...Array.from({length:14},(_,i)=>({id:'person-'+i,label:['예시 민준','예시 서연','예시 윤서','예시 지우'][i%4]+(i>3?' '+i:''),category:'대화 상대',importance:70-i})),{id:'design',label:'디자인 이야기',category:'대화방',importance:88},{id:'books',label:'책을 읽는 사람들',category:'대화방',importance:83},{id:'writing',label:'글쓰기',category:'대화 주제',importance:81},{id:'ai',label:'AI와 일상',category:'대화 주제',importance:76}];
const edges=[...['people','rooms','topics'].map(target=>({source:'root',target,relation:'contains',weight:2})),...nodes.filter(n=>n.category!=='collection').map(n=>({source:n.category==='대화 상대'?'people':n.category==='대화방'?'rooms':'topics',target:n.id,relation:'contains',weight:1})),{source:'design',target:'person-0',relation:'participates_in',weight:1}];
export const action:typeof fetchSettingsAction=async(kind,input={})=>{
  const q=input.query?.startsWith('{')?JSON.parse(input.query):{};
  if(kind==='knowledge-graph')return {ok:true,nodes,edges,stale:false,node_count:nodes.length,osk:{state:'ready',engine:'example',synced_at:at,pending:0,conflicts:0}};
  if(kind==='knowledge-graph-status')return {ok:true,stale:false};
  if(kind==='knowledge-graph-focus')return {ok:true,facts:['개발 화면의 예시 지식입니다. 실제 대화가 아닙니다.'],
    details:{node_id:input.nodeId,summary:'개발 화면의 예시입니다. 디자인 이야기에서 대화 기록과 탐색 화면에 관한 의견을 나눈 참여자입니다.',basis:'snapshot',scope_room_id:'101',key_facts:[]},
    sources:['채팅방마다 지난 대화를 날짜순으로 읽을 수 있으면 좋겠습니다.','이름을 선택하면 어떤 사람인지 원문과 함께 살펴보고 싶습니다.','설정은 한곳에서 관리하고 그래프는 넓게 보여 주세요.'].map((content,i)=>({source_id:`kakao:${'1'.repeat(64)}:room:101:log:${i+1}`,source_kind:'local_db_snapshot',chat_id:'101',author_id:'3',sender:'예시 서연',room_title:'예시 · 디자인 이야기',date:new Date(at*1000-i*600000).toISOString(),content,source_role:'peer_history'}))};
  if(kind==='history-rooms')return {ok:true,account:'synthetic-preview',rooms};
  if(kind==='history-messages'){const high=q.before?Number(q.before)-1:1000;const low=Math.max(1,high-99);return {ok:true,anchor_log_id:'1000',total:1000,next_before:low>1?String(low):null,messages:Array.from({length:high-low+1},(_,i)=>({id:String(low+i),chat_id:input.chatId,author_id:'fixture',is_self:(low+i)%3===0,sender:(low+i)%3===0?'나':'예시 서연',text:(low+i)%4===0?'말씀하신 방향으로 자료를 정리했습니다.\n필요한 맥락부터 이어서 살펴보겠습니다.':'지난 대화의 내용도 날짜순으로 읽을 수 있으면 좋겠습니다. · 예시 '+(low+i),type:1,sent_at:at-(1000-low-i)*600}))};}
  if(kind==='voice-history-sessions')return {ok:true,items:[{id:'voice-demo',title:'예시 · 오늘 나눈 이야기',started_at:at,messages:2}]};
  if(kind==='reply-history'||kind==='geeknews-history') {
    const news=kind==='geeknews-history';
    let items=Array.from({length:3},(_,i)=>({id:`${news?'news':'reply'}-${i}`,room_id:i===1?'202':'101',room:rooms[i===1?1:0].chat_name,at:at-i*3600,
      outcome:['confirmed','unknown','waiting'][i],sender:news?'올든':'예시 서연',incoming:news?'':'지난 대화에서 정한 화면 구성은 어떻게 됐나요?',
      reply:news?'GeekNews TOP5 · 예시\n\n1. 예시 기사 제목 https://news.hada.io/topic?id=123\n\n2. 두 번째 예시 소식 https://news.hada.io/topic?id=124':'대화 기록은 채팅방별로, 자동 답변과 뉴스 전송 기록은 각각 확인하도록 정리했습니다.',
      context:news?[]:[{sender:'예시 민준',text:'대화방마다 어떤 맥락으로 답했는지 알 수 있으면 좋겠어요.',sent_at:at-900},{sender:'예시 서연',text:'답변과 뉴스 전송 결과도 따로 읽고 싶어요.',sent_at:at-600}],
      articles:news?[{id:'123',title:'예시 기사 제목',url:'https://news.hada.io/topic?id=123'},{id:'124',title:'두 번째 예시 소식',url:'https://news.hada.io/topic?id=124'}]:[],slot:news?'GeekNews TOP5 · 예시':''}));
    if(q.status&&q.status!=='all')items=items.filter(item=>item.outcome===q.status);
    if(q.search)items=items.filter(item=>JSON.stringify(item).includes(q.search));
    return {ok:true,items,total:items.length,next:null,partial:false};
  }
  if(kind==='voice-history-messages')return {ok:true,next:null,items:[{turn_id:1,role:'user',content:'오늘 이야기한 내용을 기억해 줘.',created_at:at-60},{turn_id:1,role:'assistant',content:'핵심 내용을 정리해 두었습니다.',created_at:at-50}]};
  if(kind==='db-sync-history')return {ok:true,current:{phase:'complete',updated_at:at},next:null,items:[{id:3,at,phase:'complete',details:{nodes:50}},{id:2,at:at-10,phase:'graphing',details:{nodes:50}},{id:1,at:at-14,phase:'collecting',details:{rooms:2}}]};
  if(kind==='room-catalog')return {ok:true,rooms:catalog};
  if(kind==='room-upsert'){const row={chat_id:Number(input.chatId),...q};catalog=[...catalog.filter(r=>r.chat_id!==row.chat_id),row];return {ok:true};}
  if(kind==='room-delete'){catalog=catalog.filter(r=>r.chat_id!==Number(input.chatId));return {ok:true,rooms:catalog};}
  return {ok:true};
};
export const snapshot=async()=>{
  const preview=new URLSearchParams(location.search).get('orb-demo');
  const base={...unavailableSnapshot(),available:true,errorCode:null,replyModelId:selectedModel};
  if(preview==='searching')return {...base,pipeline:{...base.pipeline,active:true,stage:'context' as const},jobLoad:.35};
  if(preview==='listening'||preview==='speaking'||preview==='generating')return {...base,voice:{...base.voice,available:true,state:preview==='listening'?'user_listen':preview,rms:.16,outputRms:.25,updatedAt:Date.now()/1000}};
  return base;
};
export const pause=async()=>{emergency={schemaVersion:1,epoch:emergency.epoch+1,latched:true,reason:'example_pause'};return emergency;};
export const resume=async()=>{emergency={schemaVersion:1,epoch:emergency.epoch+1,latched:false,reason:'human_resume'};return emergency;};
