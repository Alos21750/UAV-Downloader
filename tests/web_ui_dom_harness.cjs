// Run the actual served JavaScript against a tiny, deterministic DOM model.
// This is not a renderer or a second implementation of the queue UI.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
let document;
class Element {
  constructor(tag) { this.tagName=tag; this.children=[]; this.parentNode=null; this.dataset={}; this.open=false; this.className=''; this.value=''; this.textContent=''; }
  append(...nodes) { for (const child of nodes) { if (child.tagName==='#fragment') this.append(...child.children); else {child.parentNode=this;this.children.push(child);} } }
  contains(node) { return node===this || this.children.some(child=>child.contains(node)); }
  replaceChildren(...nodes) { if(this.contains(document.activeElement))document.activeElement=document.body;for(const child of this.children)child.parentNode=null;this.children=[];this.append(...nodes); }
  querySelectorAll(selector) {
    const matches = node => selector[0]==='.' ? node.className.split(' ').includes(selector.slice(1)) : selector==='[data-control]' ? Object.hasOwn(node.dataset,'control') : node.tagName===selector;
    const out=[];for(const child of this.children){if(matches(child))out.push(child);out.push(...child.querySelectorAll(selector));}return out;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  focus(options) { this.focusOptions=options;document.activeElement=this; }
  setAttribute(key,value) { this[key]=value; }
}
const elements=new Map();
document={body:new Element('body'), documentElement:{lang:'zh-Hant'},activeElement:null,
  getElementById(id){if(!elements.has(id))elements.set(id,new Element('div'));return elements.get(id);},
  createElement(tag){return new Element(tag);},createDocumentFragment(){return new Element('#fragment');},querySelectorAll(){return [];}};
document.activeElement=document.body;
let requests=0;
const context={document, console, setTimeout:()=>0,clearTimeout:()=>{},confirm:()=>false,
  fetch:()=>{requests++;throw new Error('Rendering must not send API requests');}};
vm.createContext(context);
vm.runInContext(input.source, context);
const makeJob=(id,status='pending')=>({id,url:'https://jable.tv/videos/test-'+id+'/',status,done:0,total:0,speed_bps:0,unit:'preparing',resolution:'720',logs:['queued']});
const jobs=[makeJob('one'),makeJob('two')];
const snapshot={jobs,halted:false,worker_limit:1};
const untouched=JSON.stringify(snapshot);
context.render(snapshot);
const container=document.getElementById('jobs');
const cards=()=>container.querySelectorAll('.job');
const first=cards()[0], second=cards()[1];
first.querySelector('details').open=true;
if(input.scenario==='session-races'){
  (async()=>{
    const pending=[];
    context.fetch=()=>new Promise(resolve=>pending.push(resolve));
    const reply=(i,status,data)=>pending[i]({status,ok:status===200,json:async()=>data});
    const connect=()=>{elements.get('token').value='fixture';return elements.get('connect').onclick();};
    let p=connect();reply(0,200,snapshot);await p;
    const old=context.refresh(), recent=context.refresh();
    reply(2,200,{jobs:[jobs[1]],halted:false});await recent;
    reply(1,200,snapshot);await old;
    assert.deepEqual(cards().map(c=>c.dataset.jobId),['two']);
    const stale=context.refresh();context.disconnect();
    p=connect();reply(4,200,snapshot);await p;
    reply(3,401,{});await stale;
    assert.equal(elements.get('workspace').hidden,false,'An old session disconnected the new one');
    const afterLogout=context.refresh();context.disconnect();reply(5,200,snapshot);await afterLogout;
    assert.equal(cards().length,0,'Old response repopulated after logout');
    console.log('PASS '+input.scenario);
  })().catch(err=>{console.error(err);process.exitCode=1;});
  return;
}
switch(input.scenario){
  case 'unchanged-snapshot':
    context.render(JSON.parse(JSON.stringify(snapshot)));
    assert.equal(cards()[0],first,'Identical poll rebuilt the job DOM');
    break;
  case 'reset-snapshot':
    context.disconnect();
    assert.equal(cards().length,0);
    context.render(snapshot);
    assert.equal(cards().length,2,'Reconnecting must redraw an identical queue');
    break;
  case 'logs':
    context.render(snapshot);
    assert.equal(cards()[0].querySelector('details').open,true,'Expanded event log collapsed on refresh');
    assert.equal(cards()[1].querySelector('details').open,false,'Closed event log unexpectedly opened');
    cards()[0].querySelector('details').open=false;
    context.render(snapshot);
    assert.equal(cards()[0].querySelector('details').open,false,'Manually closed log reopened');
    break;
  case 'button-focus':
    first.querySelector('button').focus();context.render(snapshot);
    assert.equal(document.activeElement,cards()[0].querySelector('button'),'Queue action lost keyboard focus');
    assert.equal(cards()[0],first,'Unchanged poll must not steal/restore native focus');
    break;
  case 'summary-focus':
    first.querySelector('summary').focus();context.render(snapshot);
    assert.equal(document.activeElement,cards()[0].querySelector('summary'),'Event-log summary lost keyboard focus');
    assert.equal(cards()[0].querySelector('details').open,true);
    break;
  case 'outside-focus': {
    const input=document.getElementById('urls');input.value='unfinished URL';input.focus();context.render(snapshot);
    assert.equal(document.activeElement,input,'Polling stole focus from URL entry');
    assert.equal(input.value,'unfinished URL');break;}
  case 'reorder':
    first.querySelector('summary').focus();context.render({jobs:[jobs[1],jobs[0]],halted:false});
    assert.equal(cards()[0].querySelector('details').open,false);
    assert.equal(cards()[1].querySelector('details').open,true,'Open log followed array index instead of job ID');
    assert.equal(document.activeElement,cards()[1].querySelector('summary'));break;
  case 'changed-action':
    first.querySelector('button').focus();context.render({jobs:[makeJob('one','cancelled'),jobs[1]],halted:false});
    assert.equal(document.activeElement,cards()[0].querySelector('summary'),'Removed cancel button needs a stable focus fallback');break;
  default: throw new Error('Unknown scenario');
}
assert.equal(JSON.stringify(snapshot),untouched,'Rendering mutated queue response state');
assert.equal(requests,0,'Rendering triggered unintended API mutation');
console.log('PASS '+input.scenario);
