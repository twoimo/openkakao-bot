/** A bounded DOM window with measured row heights; content is never truncated. */
export class VirtualList<T> {
  private items: T[] = [];
  private readonly heights = new Map<string, number>();
  private offsets: number[] = [0];
  private readonly surface = document.createElement("div");
  private readonly observer: ResizeObserver | null;
  private frame: number | null = null;
  private disposed = false;
  private endPinned = false;
  private visible = true;
  private viewportWidth = 0;
  private viewportHeight = 0;
  get followsLatest():boolean { return this.endPinned; }
  constructor(private host: HTMLElement, private key: (item:T)=>string, private render: (item:T)=>HTMLElement, private estimate=78) {
    this.surface.style.position="relative"; host.replaceChildren(this.surface);
    this.observer=typeof ResizeObserver==='undefined'?null:new ResizeObserver(entries=>{
      let changed=false;
      const anchor=this.anchor();
      for(const entry of entries){
        if(entry.target===this.host){
          const {width,height}=entry.contentRect;
          if(width>0&&height>0&&(Math.abs(width-this.viewportWidth)>1||Math.abs(height-this.viewportHeight)>1)){
            this.viewportWidth=width;this.viewportHeight=height;changed=true;
          }
          continue;
        }
        const id=(entry.target as HTMLElement).dataset.virtualKey!;const height=entry.borderBoxSize?.[0]?.blockSize || entry.contentRect.height;
        if(height>0 && Math.abs((this.heights.get(id)??this.estimate)-height)>1){this.heights.set(id,height);changed=true;}}
      if(changed){this.measure();if(this.endPinned)this.host.scrollTop=this.offsets.at(-1)!;else this.restore(anchor);this.schedule();}
    });
    host.addEventListener('scroll',this.scroll,{passive:true});
  }
  set(items:T[],options:{end?:boolean;preserve?:boolean}={}):void {
    const anchor=options.preserve?this.anchor():null;
    this.items=items;this.measure();this.endPinned=options.end===true;
    if(options.end)this.host.scrollTop=this.offsets.at(-1)!;else if(options.preserve)this.restore(anchor);else this.host.scrollTop=0;
    this.draw();
  }
  private anchor():{key:string;within:number}|null {
    if(!this.items.length)return null;
    let low=0,high=this.items.length;
    while(low<high){const mid=(low+high)>>>1;if(this.offsets[mid]<=this.host.scrollTop)low=mid+1;else high=mid;}
    const index=Math.max(0,low-1);
    return {key:this.key(this.items[index]),within:this.host.scrollTop-this.offsets[index]};
  }
  private restore(anchor:{key:string;within:number}|null):void {
    if(!anchor)return;
    const index=this.items.findIndex(item=>this.key(item)===anchor.key);
    if(index>=0)this.host.scrollTop=this.offsets[index]+anchor.within;
  }
  setVisible(visible:boolean):void {
    if(this.visible===visible||this.disposed)return;
    this.visible=visible;
    if(visible)this.draw();else{this.observer?.disconnect();if(this.frame!==null)cancelAnimationFrame(this.frame);this.frame=null;}
  }
  private measure():void {this.offsets=[0];for(const item of this.items)this.offsets.push(this.offsets.at(-1)!+(this.heights.get(this.key(item))??this.estimate));this.surface.style.height=this.offsets.at(-1)+'px';}
  private readonly scroll=():void=>{this.endPinned=this.host.scrollTop+this.host.clientHeight>=this.offsets.at(-1)!-12;this.schedule();};
  private schedule():void {if(this.frame!==null||this.disposed||!this.visible)return;this.frame=requestAnimationFrame(()=>{this.frame=null;this.draw();});}
  private draw():void {
    if(this.disposed||!this.visible)return;this.observer?.disconnect();this.surface.replaceChildren();
    this.observer?.observe(this.host);
    let low=0,high=this.items.length;const top=this.host.scrollTop;
    while(low<high){const mid=(low+high)>>>1;if(this.offsets[mid]<top)low=mid+1;else high=mid;}
    const start=Math.max(0,low-5);const bottom=top+Math.max(this.host.clientHeight,400)+500;
    for(let i=start;i<this.items.length&&i<start+80;i++){
      if(this.offsets[i]>bottom)break;const row=this.render(this.items[i]);row.dataset.virtualKey=this.key(this.items[i]);
      row.style.position='absolute';row.style.top=this.offsets[i]+'px';row.style.left='0';row.style.right='0';
      this.surface.append(row);this.observer?.observe(row);
    }
  }
  dispose():void {this.disposed=true;this.host.removeEventListener('scroll',this.scroll);this.observer?.disconnect();if(this.frame!==null)cancelAnimationFrame(this.frame);this.surface.replaceChildren();}
}
