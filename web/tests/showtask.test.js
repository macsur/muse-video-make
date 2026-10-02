/*
 * showTask() 渲染回归测试：  node web/tests/showtask.test.js
 *
 * 用桩 document 把 index.html 里的 IIFE 跑起来，直接断言 showTask 的产物 HTML。
 * 为什么单独测：最容易出错的是「合成失败」那条分支 —— result.url 为空但
 * status 仍是 completed，如果只写 `status==='completed'&&url` 一个判断，
 * 页面会掉进"生成中"分支显示一个永远 100% 的进度条，用户以为还在跑。
 * 纯静态看代码看不出来，必须真的把 HTML 渲出来比对。
 *
 * 零依赖：只用 node 内置能力，不需要装 jsdom，不需要浏览器。
 */
const fs=require('fs');
const h=fs.readFileSync('web/index.html','utf8');
const body=h.match(/<script>([\s\S]*?)<\/script>/)[1];

// 只取 IIFE 内部，暴露 showTask 供断言
const els={get taskArea(){return globalThis.__els.taskArea;},get taskHint(){return globalThis.__els.taskHint;}};
const $=(id)=>globalThis.document.getElementById(id);
const state={base:'http://127.0.0.1:18610',size:'9:16',tasks:[]};
globalThis.localStorage={getItem:()=>null,setItem:()=>{},removeItem:()=>{}};
globalThis.fetch=()=>Promise.reject(new Error('no network in test'));
globalThis.__els={};
globalThis.document={getElementById:(id)=>globalThis.__els[id]||(globalThis.__els[id]={id,innerHTML:'',textContent:'',className:'',dataset:{},style:{},classList:{add(){},remove(){},toggle(){}},value:'5',addEventListener(){},checked:false,files:[]}),querySelector:()=>null,querySelectorAll:()=>[],addEventListener(){}};
globalThis.window=globalThis; globalThis.navigator={userAgent:'node'};
globalThis.location={origin:'http://127.0.0.1:8090'};
const out={};
const fns={esc:s=>String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),
  fmtBytes:n=>n+'B'};
const src=body.replace(/^\s*\/\/ \[Local Auto-Init\][\s\S]*?$/m,'')
  .replace('(function () {','(function () { globalThis.__T={showTask,segmentsHtml,$:$,state};');
new Function('$','state','els', src)($,state,els);
const {showTask,segmentsHtml}=globalThis.__T;

let fail=0;
const check=(n,c,d='')=>{ console.log((c?'  PASS ':'  FAIL ')+n+(c?'':' '+d)); if(!c)fail++; };

// 1) 单段成功
showTask({id:'t1',status:'completed',url:'/v1/media/a.mp4',prompt:'一只猫',duration:5,size:'9:16',bytes:1024});
let o=els.taskArea.innerHTML;
check('单段完成：渲染 video', o.includes('<video src="http://127.0.0.1:18610/v1/media/a.mp4"'));
check('单段完成：不出现分段列表', !o.includes('分段结果'));
check('单段完成：显示下载按钮', o.includes('下载视频'));

// 2) 长视频合成成功
showTask({id:'t2',status:'completed',url:'/v1/media/merged_t2.mp4',prompt:'长片',duration:240,
  size:'9:16',bytes:9e6,segmentTotal:10,segmentDone:10,mergeReason:''});
o=els.taskArea.innerHTML;
check('长视频合成：渲染 video', o.includes('merged_t2.mp4'));
check('长视频合成：显示合成说明', o.includes('由 10 个短片合成'));

// 3) 合成失败降级（最容易出错的一条）
showTask({id:'t3',status:'completed',url:'',prompt:'长片',duration:240,size:'9:16',
  segmentTotal:10,segmentDone:10,mergeReason:'未找到 ffmpeg',
  segments:[{index:1,url:'/v1/media/s1.mp4',seconds:28,bytes:111},
            {index:2,url:'/v1/media/s2.mp4',seconds:28,bytes:222}]});
o=els.taskArea.innerHTML;
check('降级：不落到"生成中"分支', !o.includes('正在保存'));
check('降级：标题正确', o.includes('分段已生成，未合成'));
check('降级：给出 ffmpeg 原因', o.includes('未找到 ffmpeg'));
check('降级：渲染 2 个分段', (o.match(/第 \d 段/g)||[]).length===2, o);
check('降级：分段 URL 绝对化', o.includes('http://127.0.0.1:18610/v1/media/s1.mp4'));
check('降级：显示各段时长', o.includes('28s'));

// 4) 生成中
showTask({id:'t4',status:'processing',progress:47,prompt:'长片',duration:240,
  segmentTotal:10,segmentDone:4,segmentIndex:5});
o=els.taskArea.innerHTML;
check('生成中：显示第 k/N 段', o.includes('第 4/10 段') && o.includes('正在生成第 5 段'));
check('生成中：真实进度不伪造', o.includes('47%'));
check('生成中：长视频文案正确', o.includes('长视频会逐段生成'));

// 5) 失败但保留分段
showTask({id:'t5',status:'failed',err:'第3段失败（已完成 2/6 段）',prompt:'长片',
  segmentTotal:6,segmentDone:2,segments:[{index:1,url:'/v1/media/s1.mp4',seconds:18,bytes:1}]});
o=els.taskArea.innerHTML;
check('失败：显示错误', o.includes('已完成 2/6 段'));
check('失败：仍列出已完成的分段', o.includes('第 1 段'));

// 6) XSS：提示词里的尖括号不能被当 HTML
showTask({id:'t6',status:'completed',url:'/v1/media/a.mp4',prompt:'<img src=x onerror=alert(1)>',duration:5});
o=els.taskArea.innerHTML;
check('XSS：提示词被转义', !o.includes('<img src=x') && o.includes('&lt;img'));

// 7) 短任务文案不受污染
showTask({id:'t7',status:'processing',progress:10,prompt:'短',duration:5});
o=els.taskArea.innerHTML;
check('短任务：仍是 1~2 分钟文案', o.includes('通常需要 1～2 分钟'));

console.log(fail? '\nFAILED '+fail+' 项' : '\nPASS 全部通过');
process.exit(fail?1:0);
