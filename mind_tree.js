/* Left-rooted tree layout for the mind map. Pure arithmetic; no DOM, no stored data. */
const MindTree = (() => {
  "use strict";
  const DEFAULTS={rowHeight:30,rowGap:8,levelGap:48,groupGap:12,levelWidths:[220,250,290,320]};
  // Every visible node takes one row; a parent sits level with the middle of its
  // children. Collapsed nodes are leaves, so hidden branches cost no space.
  function layout(root,options={}) {
    const {rowHeight,rowGap,levelGap,groupGap,levelWidths}={...DEFAULTS,...options};
    const widthAt=depth=>levelWidths[Math.min(depth,levelWidths.length-1)];
    const xAt=depth=>{let x=0;for(let level=0;level<depth;level++)x+=widthAt(level)+levelGap;return x;};
    const nodes=[],links=[];
    let cursor=0,maxDepth=0;
    function place(node,depth,parent) {
      maxDepth=Math.max(maxDepth,depth);
      const entry={node,depth,parent,x:xAt(depth),y:0,width:widthAt(depth),height:rowHeight};
      nodes.push(entry);
      const children=node.expanded?(node.children || []):[];
      if(!children.length) {entry.y=cursor;cursor+=rowHeight+rowGap;return entry;}
      const placed=[];
      const branching=child=>Boolean(child.expanded && child.children?.length);
      for(const [index,child] of children.entries()) {
        // An open branch gets breathing room from its neighbours, like groups in a hand-drawn map.
        if(depth===0 && index>0 && branching(child) && !branching(children[index-1]))cursor+=groupGap;
        // Links stay in reading order: a branch's link comes before its children's.
        const link={from:entry,to:null};links.push(link);
        link.to=place(child,depth+1,entry);placed.push(link.to);
        if(depth===0 && branching(child) && index<children.length-1)cursor+=groupGap;
      }
      entry.y=(placed[0].y+placed[placed.length-1].y)/2;
      return entry;
    }
    place(root,0,null);
    return {nodes,links,width:xAt(maxDepth)+widthAt(maxDepth),height:Math.max(rowHeight,cursor-rowGap)};
  }
  // "1", "1.2", "1.2.3": numbering follows the parent's label.
  const number=(parent,index)=>parent?`${parent}.${index+1}`:String(index+1);
  // Camera: a tree point p is shown at p*k + (x,y) inside a viewport of the given size.
  function fit(view,size,{min=.5,max=1,pad=24,focusY=null}={}) {
    const k=Math.max(min,Math.min(max,(view.width-2*pad)/Math.max(1,size.width),(view.height-2*pad)/Math.max(1,size.height)));
    const width=size.width*k,height=size.height*k;
    const x=width<=view.width-2*pad?(view.width-width)/2:pad;
    let y=height<=view.height-2*pad?(view.height-height)/2:pad;
    // A tree taller than the viewport opens around its focus row (the root) instead of its top.
    if(height>view.height-2*pad && focusY!==null)y=Math.min(pad,Math.max(view.height-pad-height,view.height/2-focusY*k));
    return {x,y,k};
  }
  function zoomAt(camera,point,factor,{min=.25,max=2.5}={}) {
    const k=Math.max(min,Math.min(max,camera.k*factor)),ratio=k/camera.k;
    return {x:point.x-(point.x-camera.x)*ratio,y:point.y-(point.y-camera.y)*ratio,k};
  }
  // The smallest move that brings a tree-space box into view; a box too large to fit shows its start.
  function reveal(camera,view,box,{pad=24}={}) {
    const left=box.x*camera.k+camera.x,top=box.y*camera.k+camera.y,width=box.width*camera.k,height=box.height*camera.k;
    let {x,y}=camera;
    if(width>view.width-2*pad)x=pad-box.x*camera.k;
    else if(left<pad)x+=pad-left;
    else if(left+width>view.width-pad)x-=left+width-(view.width-pad);
    if(height>view.height-2*pad)y=pad-box.y*camera.k;
    else if(top<pad)y+=pad-top;
    else if(top+height>view.height-pad)y-=top+height-(view.height-pad);
    return {x,y,k:camera.k};
  }
  // Part of the tree always stays inside the viewport, so it cannot be dragged out of reach.
  function clamp(camera,view,size,{keep=48}={}) {
    return {x:Math.min(view.width-keep,Math.max(keep-size.width*camera.k,camera.x)),
      y:Math.min(view.height-keep,Math.max(keep-size.height*camera.k,camera.y)),k:camera.k};
  }
  function bounds(entries) {
    const x=Math.min(...entries.map(entry=>entry.x)),y=Math.min(...entries.map(entry=>entry.y));
    return {x,y,width:Math.max(...entries.map(entry=>entry.x+entry.width))-x,height:Math.max(...entries.map(entry=>entry.y+entry.height))-y};
  }
  // Visible nodes in reading order, for keyboard walks and tests.
  function visible(root) {
    const result=[];
    (function walk(node){result.push(node);if(node.expanded)for(const child of node.children || [])walk(child);})(root);
    return result;
  }
  return {layout,number,visible,fit,zoomAt,reveal,clamp,bounds};
})();
if(typeof module!=="undefined" && module.exports)module.exports=MindTree;
