/**
 * 分层布局：主链层依序排布，旁挂组与主链末层并排。
 * TB（纵向）层内横排换行；LR（横向）层内纵排。
 * 返回 ReactFlow 节点（父组 + 子节点，子节点坐标相对父组）。
 */

import type { Direction, LayerSpec, NodeSpec } from './data/graph'

export interface LayoutResult {
  /** 父组节点（type: layerGroup，自定义渲染层头） */
  parents: LayoutParent[]
  /** 子节点（parentId 指向父组，坐标相对父组） */
  children: LayoutChild[]
  /** 画布总尺寸（fitView 与 MiniMap 参考） */
  width: number
  height: number
}

export interface LayoutParent {
  id: string
  x: number
  y: number
  width: number
  height: number
  title: string
  subtitle?: string
}

export interface LayoutChild {
  spec: NodeSpec
  parentId: string
  x: number
  y: number
}

const NODE_W = 208
const NODE_H = 92

/** 节点设计尺寸（App 构建子节点时作为显式 style，保证测量值与布局假设一致） */
export const NODE_SIZE = { width: NODE_W, height: NODE_H }
const NODE_GAP_X = 20
const NODE_GAP_Y = 16
const GROUP_PAD_X = 20
const GROUP_PAD_TOP = 52
const GROUP_PAD_BOTTOM = 18
const GROUP_GAP_X = 48
const GROUP_GAP_Y = 96
/** TB 模式层内每行最多节点数 */
const MAX_PER_ROW = 4

function rowsFor(count: number, perRow: number): number {
  return Math.ceil(count / perRow)
}

export function computeLayout(
  layers: LayerSpec[],
  direction: Direction,
): LayoutResult {
  const parents: LayoutParent[] = []
  const children: LayoutChild[] = []

  if (direction === 'TB') {
    // 主链与旁挂分两条带，旁挂层在主链下方并排
    const main = layers.filter((l) => !l.side)
    const side = layers.filter((l) => l.side)
    let cursorY = 0
    let maxWidth = 0

    for (const layer of main) {
      const cols = Math.min(layer.nodes.length, MAX_PER_ROW)
      const rows = rowsFor(layer.nodes.length, MAX_PER_ROW)
      const width = cols * NODE_W + (cols - 1) * NODE_GAP_X + GROUP_PAD_X * 2
      const height =
        GROUP_PAD_TOP + rows * NODE_H + (rows - 1) * NODE_GAP_Y + GROUP_PAD_BOTTOM
      parents.push({
        id: layer.id,
        x: 0,
        y: cursorY,
        width,
        height,
        title: layer.title,
        subtitle: layer.subtitle,
      })
      layer.nodes.forEach((spec, i) => {
        const row = Math.floor(i / MAX_PER_ROW)
        const col = i % MAX_PER_ROW
        children.push({
          spec,
          parentId: layer.id,
          x: GROUP_PAD_X + col * (NODE_W + NODE_GAP_X),
          y: GROUP_PAD_TOP + row * (NODE_H + NODE_GAP_Y),
        })
      })
      maxWidth = Math.max(maxWidth, width)
      cursorY += height + GROUP_GAP_Y
    }

    // 旁挂层并排一行
    let sideX = 0
    let sideMaxH = 0
    for (const layer of side) {
      const rows = rowsFor(layer.nodes.length, MAX_PER_ROW)
      const cols = Math.min(layer.nodes.length, MAX_PER_ROW)
      const width = cols * NODE_W + (cols - 1) * NODE_GAP_X + GROUP_PAD_X * 2
      const height =
        GROUP_PAD_TOP + rows * NODE_H + (rows - 1) * NODE_GAP_Y + GROUP_PAD_BOTTOM
      parents.push({
        id: layer.id,
        x: sideX,
        y: cursorY,
        width,
        height,
        title: layer.title,
        subtitle: layer.subtitle,
      })
      layer.nodes.forEach((spec, i) => {
        const row = Math.floor(i / MAX_PER_ROW)
        const col = i % MAX_PER_ROW
        children.push({
          spec,
          parentId: layer.id,
          x: GROUP_PAD_X + col * (NODE_W + NODE_GAP_X),
          y: GROUP_PAD_TOP + row * (NODE_H + NODE_GAP_Y),
        })
      })
      sideX += width + GROUP_GAP_X
      sideMaxH = Math.max(sideMaxH, height)
      maxWidth = Math.max(maxWidth, sideX)
    }
    cursorY += sideMaxH

    return {
      parents,
      children,
      width: maxWidth,
      height: cursorY,
    }
  }

  // LR：层从左到右，层内纵排；旁挂层并排两列
  const main = layers.filter((l) => !l.side)
  const side = layers.filter((l) => l.side)
  let cursorX = 0
  let maxHeight = 0

  for (const layer of main) {
    const rows = layer.nodes.length
    const width = NODE_W + GROUP_PAD_X * 2
    const height =
      GROUP_PAD_TOP + rows * NODE_H + (rows - 1) * NODE_GAP_Y + GROUP_PAD_BOTTOM
    parents.push({
      id: layer.id,
      x: cursorX,
      y: 0,
      width,
      height,
      title: layer.title,
      subtitle: layer.subtitle,
    })
    layer.nodes.forEach((spec, i) => {
      children.push({
        spec,
        parentId: layer.id,
        x: GROUP_PAD_X,
        y: GROUP_PAD_TOP + i * (NODE_H + NODE_GAP_Y),
      })
    })
    maxHeight = Math.max(maxHeight, height)
    cursorX += width + GROUP_GAP_X
  }

  let sideY = 0
  let sideMaxW = 0
  for (const layer of side) {
    const rows = layer.nodes.length
    const width = NODE_W + GROUP_PAD_X * 2
    const height =
      GROUP_PAD_TOP + rows * NODE_H + (rows - 1) * NODE_GAP_Y + GROUP_PAD_BOTTOM
    parents.push({
      id: layer.id,
      x: cursorX,
      y: sideY,
      width,
      height,
      title: layer.title,
      subtitle: layer.subtitle,
    })
    layer.nodes.forEach((spec, i) => {
      children.push({
        spec,
        parentId: layer.id,
        x: GROUP_PAD_X,
        y: GROUP_PAD_TOP + i * (NODE_H + NODE_GAP_Y),
      })
    })
    sideY += height + GROUP_GAP_Y
    sideMaxW = Math.max(sideMaxW, width)
    maxHeight = Math.max(maxHeight, sideY)
  }
  cursorX += sideMaxW

  return {
    parents,
    children,
    width: cursorX,
    height: maxHeight,
  }
}
