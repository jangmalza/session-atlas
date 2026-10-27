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
  // Visible nodes in reading order, for keyboard walks and tests.
  function visible(root) {
    const result=[];
    (function walk(node){result.push(node);if(node.expanded)for(const child of node.children || [])walk(child);})(root);
    return result;
  }
  return {layout,number,visible};
})();
if(typeof module!=="undefined" && module.exports)module.exports=MindTree;
