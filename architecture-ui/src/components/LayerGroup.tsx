import type { Node, NodeProps } from '@xyflow/react'

export interface LayerGroupData extends Record<string, unknown> {
  title: string
  subtitle?: string
}

export type LayerGroupNode = Node<LayerGroupData, 'layerGroup'>

/** 层分组容器：仅渲染层头，节点由子节点绝对定位填充 */
export function LayerGroup({ data }: NodeProps<LayerGroupNode>) {
  return (
    <div className="layer-group">
      <div className="layer-group-head">
        <div className="layer-group-title">{data.title}</div>
        {data.subtitle && <div className="layer-group-subtitle">{data.subtitle}</div>}
      </div>
    </div>
  )
}
