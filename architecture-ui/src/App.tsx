import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  useNodesInitialized,
  useReactFlow,
  type Edge,
  type Node,
} from '@xyflow/react'
import {
  EDGES,
  KIND_META,
  LAYERS,
  type Direction,
  type EdgeSpec,
  type NodeKind,
} from './data/graph'
import { computeLayout, NODE_SIZE } from './layout'
import { DataCard, type DataCardData } from './components/DataCard'
import { LayerGroup, type LayerGroupData } from './components/LayerGroup'

const nodeTypes = { dataCard: DataCard, layerGroup: LayerGroup }

const KIND_ORDER: NodeKind[] = ['comp', 'llm', 'gate', 'guard', 'store', 'ext']

interface Reach {
  upstream: Set<string>
  downstream: Set<string>
}

/** 选中节点的有向传递闭包（全部上游 + 全部下游） */
function computeReach(selected: string | null, edges: EdgeSpec[]): Reach | null {
  if (!selected) return null
  const upstream = new Set<string>([selected])
  const downstream = new Set<string>([selected])
  let changed = true
  while (changed) {
    changed = false
    for (const e of edges) {
      if (downstream.has(e.source) && !downstream.has(e.target)) {
        downstream.add(e.target)
        changed = true
      }
      if (upstream.has(e.target) && !upstream.has(e.source)) {
        upstream.add(e.source)
        changed = true
      }
    }
  }
  return { upstream, downstream }
}

function Canvas() {
  const [direction, setDirection] = useState<Direction>('TB')
  const [hidden, setHidden] = useState<Set<string>>(() => new Set())
  const [selected, setSelected] = useState<string | null>(null)
  const { fitView } = useReactFlow()

  const visibleLayers = useMemo(
    () => LAYERS.filter((layer) => !hidden.has(layer.id)),
    [hidden],
  )
  const visibleIds = useMemo(
    () => new Set(visibleLayers.flatMap((layer) => layer.nodes.map((n) => n.id))),
    [visibleLayers],
  )

  const layout = useMemo(
    () => computeLayout(visibleLayers, direction),
    [visibleLayers, direction],
  )

  const reach = useMemo(() => computeReach(selected, EDGES), [selected])

  const nodes = useMemo<Node[]>(() => {
    const relevant = reach
      ? new Set<string>([...reach.upstream, ...reach.downstream])
      : null
    const groupNodes: Node[] = layout.parents.map((p) => ({
      id: p.id,
      type: 'layerGroup' as const,
      position: { x: p.x, y: p.y },
      data: {
        title: p.title,
        subtitle: p.subtitle,
      } as LayerGroupData,
      // 顶层 width/height 为固定尺寸语义（MiniMap 读取该字段）
      width: p.width,
      height: p.height,
      draggable: false,
      selectable: false,
    }))
    const childNodes: Node[] = layout.children.map((c) => ({
      id: c.spec.id,
      type: 'dataCard' as const,
      parentId: c.parentId,
      extent: 'parent' as const,
      position: { x: c.x, y: c.y },
      style: { width: NODE_SIZE.width, height: NODE_SIZE.height },
      width: NODE_SIZE.width,
      height: NODE_SIZE.height,
      data: {
        spec: c.spec,
        dimmed: relevant !== null && !relevant.has(c.spec.id),
        active: c.spec.id === selected,
      } as DataCardData,
      draggable: false,
    }))
    return [...groupNodes, ...childNodes]
  }, [layout, reach, selected])

  const edges = useMemo<Edge[]>(() => {
    const sourceHandle = direction === 'TB' ? 'b' : 'r'
    const targetHandle = direction === 'TB' ? 't' : 'l'
    return EDGES.filter((e) => visibleIds.has(e.source) && visibleIds.has(e.target)).map(
      (e) => {
        const onPath =
          reach !== null &&
          reach.upstream.has(e.source) &&
          reach.downstream.has(e.target)
        const dimmed = reach !== null && !onPath
        return {
          id: `${e.source}->${e.target}`,
          source: e.source,
          target: e.target,
          sourceHandle,
          targetHandle,
          type: 'smoothstep',
          label: e.label ?? undefined,
          className: [
            'flow-edge',
            e.dashed ? 'is-dashed' : '',
            dimmed ? 'is-dimmed' : '',
            onPath ? 'is-active' : '',
          ]
            .filter(Boolean)
            .join(' '),
          style: { strokeDasharray: e.dashed ? '6 4' : undefined },
        }
      },
    )
  }, [visibleIds, direction, reach])

  // 节点测量完成后、以及布局或可见层变化后重新适配视口
  const nodesInitialized = useNodesInitialized()
  useEffect(() => {
    if (!nodesInitialized) return
    const timer = window.setTimeout(() => {
      void fitView({ padding: 0.12, duration: 320 })
    }, 40)
    return () => window.clearTimeout(timer)
  }, [nodesInitialized, direction, hidden, fitView])

  const toggleLayer = useCallback((layerId: string) => {
    setHidden((prev) => {
      const next = new Set(prev)
      if (next.has(layerId)) {
        next.delete(layerId)
      } else {
        next.add(layerId)
      }
      return next
    })
    setSelected(null)
  }, [])

  const onNodeClick = useCallback((_: unknown, node: Node) => {
    const spec = (node.data as DataCardData).spec
    setSelected(spec ? spec.id : null)
  }, [])

  const onPaneClick = useCallback(() => setSelected(null), [])

  const nodeColor = useCallback((node: Node) => {
    const spec = (node.data as DataCardData).spec
    if (!spec) return '#e2e8f0'
    switch (spec.kind) {
      case 'comp':
        return '#60a5fa'
      case 'llm':
        return '#fbbf24'
      case 'gate':
        return '#fb7185'
      case 'guard':
        return '#34d399'
      case 'store':
        return '#a78bfa'
      case 'ext':
        return '#94a3b8'
    }
  }, [])

  return (
    <div className="app">
      <header className="toolbar">
        <div className="toolbar-row">
          <div className="brand">
            <h1>DataAgent 架构图</h1>
            <span className="brand-sub">受控 DSL · 确定性 SQL · 全链路治理</span>
          </div>
          <div className="direction-switch">
            <button
              type="button"
              className={direction === 'TB' ? 'on' : ''}
              onClick={() => setDirection('TB')}
            >
              纵向
            </button>
            <button
              type="button"
              className={direction === 'LR' ? 'on' : ''}
              onClick={() => setDirection('LR')}
            >
              横向
            </button>
          </div>
        </div>
        <div className="toolbar-row toolbar-row-filters">
          <div className="layer-chips">
            {LAYERS.map((layer) => (
              <button
                key={layer.id}
                type="button"
                className={`layer-chip ${hidden.has(layer.id) ? 'off' : ''}`}
                onClick={() => toggleLayer(layer.id)}
              >
                {layer.title}
              </button>
            ))}
          </div>
          <div className="legend">
            {KIND_ORDER.map((kind) => (
              <span key={kind} className={`legend-item ${KIND_META[kind].className}`}>
                <i />
                {KIND_META[kind].label}
              </span>
            ))}
          </div>
        </div>
        <div className="toolbar-hint">
          点击节点高亮上下游链路，再点空白处恢复。层标签可切换显隐。
        </div>
      </header>
      <main className="flow-wrap">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          onNodeClick={onNodeClick}
          onPaneClick={onPaneClick}
          fitView
          fitViewOptions={{ padding: 0.12 }}
          minZoom={0.15}
          maxZoom={1.6}
          proOptions={{ hideAttribution: true }}
          nodesConnectable={false}
          edgesReconnectable={false}
        >
          <Background variant={BackgroundVariant.Dots} gap={26} size={1.6} color="#cbd5e1" />
          <Controls showInteractive={false} />
          <MiniMap
            pannable
            zoomable
            bgColor="#f1f5f9"
            maskColor="rgba(148, 163, 184, 0.28)"
            nodeColor={nodeColor}
          />
        </ReactFlow>
      </main>
      <footer className="statusbar">
        口径对齐 docs/guides/architecture.md · 由 Pages workflow 自动构建部署
      </footer>
    </div>
  )
}

export default function App() {
  return (
    <ReactFlowProvider>
      <Canvas />
    </ReactFlowProvider>
  )
}
