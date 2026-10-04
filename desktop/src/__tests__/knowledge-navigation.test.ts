// @vitest-environment happy-dom
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { KnowledgeDrilldown, type KnowledgeGraph, type KnowledgeView } from "../knowledge/graph-model";
import { unavailableSnapshot } from "../contracts";
import { settingsMarkup } from "../ui";

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn(async () => null) }));
vi.mock("@tauri-apps/api/event", () => ({ listen: vi.fn(async () => () => undefined) }));
vi.mock("../knowledge/hologram", () => ({ KnowledgeHologram: class {
  private readonly model: KnowledgeDrilldown;
  constructor(_canvas: HTMLCanvasElement, private readonly graph: KnowledgeGraph,
    private readonly notify: (event: { node: KnowledgeGraph["nodes"][number] | null; view: KnowledgeView }) => void,
    private readonly disposed: () => void) { this.model = new KnowledgeDrilldown(graph); }
  get canGoBack() { return this.model.canGoBack; }
  get currentView() { return this.model.current(); }
  private publish(view: KnowledgeView) { this.notify({node:this.graph.nodes.find(n=>n.id===view.focusId)??null,view}); return view; }
  clickNode(id: string) { return this.publish(this.model.clickNode(id)); }
  expandOneHop() { return this.publish(this.model.expandOneHop()); }
  back() { return this.publish(this.model.back()); }
  reset() { return this.publish(this.model.reset()); }
  dispose() { this.disposed(); }
}}));
let setup: typeof import("../main").setupKnowledgeGraph;
beforeAll(async () => {
  document.body.innerHTML='<div id="app"></div>';
  window.history.replaceState({},"","/?view=settings");
  setup=(await import("../main")).setupKnowledgeGraph;
  await vi.waitFor(()=>expect(document.getElementById("app")?.dataset.state).not.toBe("loading"));
});
beforeEach(()=>{document.body.innerHTML=settingsMarkup();});
const payload={nodes:[{id:"a",label:"서울",importance:2},{id:"b",label:"런던",importance:1}],edges:[]};
function pending() {
  const requests:Array<{resolve:(value:Record<string,unknown>)=>void;reject:(reason:Error)=>void}>=[];
  const load=vi.fn(()=>new Promise<Record<string,unknown>>((resolve,reject)=>requests.push({resolve,reject})));
  return {requests,load};
}
const settle=async()=>{await Promise.resolve();await Promise.resolve();};
const text=()=>document.getElementById("knowledge-retrieve")!.textContent;
describe("knowledge navigation UI and retrieval epochs",()=>{
  it("restores expansion and clears overview details through visible controls",async()=>{
    const {load}=pending();const graph=(await setup(payload,unavailableSnapshot(),load))!;
    expect(document.querySelector<HTMLButtonElement>("#knowledge-back")!.disabled).toBe(true);
    graph.clickNode("a");document.querySelector<HTMLButtonElement>("#knowledge-expand-hop")!.click();
    graph.clickNode("b");document.querySelector<HTMLButtonElement>("#knowledge-back")!.click();
    expect(graph.currentView.focusId).toBe("a");expect(graph.currentView.hops).toBe(3);
    expect(document.getElementById("knowledge-focus-title")!.textContent).toBe("서울");
    document.querySelector<HTMLButtonElement>("#knowledge-overview")!.click();
    expect(document.getElementById("knowledge-relations")!.children.length).toBe(0);
    expect(text()).toBe("항목을 선택하면 관련 대화를 찾아 보여드립니다.");
  });
  it("discards the first A success and B failure after A → B → A",async()=>{
    const {load,requests}=pending();const graph=(await setup(payload,unavailableSnapshot(),load))!;
    graph.clickNode("a");graph.clickNode("b");graph.clickNode("a");
    requests[2].resolve({ok:true,facts:["최신 결과"]});await settle();
    requests[0].resolve({ok:true,facts:["오래된 결과"]});requests[1].reject(new Error("late failure"));await settle();
    expect(text()).toContain("최신 결과");expect(text()).not.toContain("오래된 결과");
  });
  it("ignores a pending result after overview and after disposal",async()=>{
    const {load,requests}=pending();const graph=(await setup(payload,unavailableSnapshot(),load))!;
    graph.clickNode("a");graph.reset();requests[0].resolve({ok:true,facts:["늦은 결과"]});await settle();
    expect(text()).toBe("항목을 선택하면 관련 대화를 찾아 보여드립니다.");
    graph.clickNode("b");const before=text();graph.dispose();requests[1].resolve({ok:true,facts:["폐기 결과"]});await settle();
    expect(text()).toBe(before);
    document.querySelector<HTMLButtonElement>("#knowledge-back")!.click();
    document.querySelector<HTMLButtonElement>("#knowledge-overview")!.click();
    document.querySelector<HTMLButtonElement>("#knowledge-expand-hop")!.click();
    expect(document.querySelector(".knowledge-a11y-node")).toBeNull();
    graph.clickNode("a");
    expect(load).toHaveBeenCalledTimes(2);expect(text()).toBe(before);
  });
});

describe("knowledge availability states",()=>{
  it("distinguishes a failed read from a confirmed empty graph",async()=>{
    expect(await setup(null,unavailableSnapshot(),vi.fn())).toBeNull();
    expect(document.getElementById("knowledge-summary")!.textContent).toContain("확인할 수 없습니다");
    expect(document.querySelector<HTMLElement>(".knowledge-hologram-shell")!.hidden).toBe(false);
    expect(document.querySelector<HTMLCanvasElement>("#knowledge-graph-canvas")!.hidden).toBe(true);
    document.body.innerHTML=settingsMarkup();
    expect(await setup({nodes:[],edges:[]},unavailableSnapshot(),vi.fn())).toBeNull();
    expect(document.getElementById("knowledge-summary")!.textContent).toBe("아직 연결된 대화가 없습니다.");
  });
});
