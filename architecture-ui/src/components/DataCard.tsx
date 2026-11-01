import { Handle, Position, type Node, type NodeProps } from '@xyflow/react'
import { KIND_META, type NodeSpec } from '../data/graph'

export interface DataCardData extends Record<string, unknown> {
  spec: NodeSpec
  dimmed: boolean
  active: boolean
}

export type DataCardNode = Node<DataCardData, 'dataCard'>

/**
 * 架构节点卡片：类型徽标 + 主标题 + 代码路径 + 关键机制。
 * 四向 Handle 由 App 按 TB/LR 布局选择对应的 sourceHandle / targetHandle。
 */
export function DataCard({ data }: NodeProps<DataCardNode>) {
  const { spec, dimmed, active } = data
  const kind = KIND_META[spec.kind]
  return (
    <div
      className={[
        'data-card',
        kind.className,
        dimmed ? 'is-dimmed' : '',
        active ? 'is-active' : '',
      ]
        .filter(Boolean)
        .join(' ')}
    >
      <span className="data-card-badge">{kind.label}</span>
      <div className="data-card-label">{spec.label}</div>
      {spec.path && <div className="data-card-path">{spec.path}</div>}
      {spec.note && <div className="data-card-note">{spec.note}</div>}
      <Handle id="t" type="target" position={Position.Top} className="card-handle" isConnectable={false} />
      <Handle id="b" type="source" position={Position.Bottom} className="card-handle" isConnectable={false} />
      <Handle id="l" type="target" position={Position.Left} className="card-handle" isConnectable={false} />
      <Handle id="r" type="source" position={Position.Right} className="card-handle" isConnectable={false} />
    </div>
  )
}
