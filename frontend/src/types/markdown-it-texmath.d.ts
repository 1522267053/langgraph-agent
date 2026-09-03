declare module 'markdown-it-texmath' {
  interface TexmathOptions {
    /** 公式渲染引擎，如 katex */
    engine: unknown
    /** 分隔符风格：dollars（$...$ / $$...$$）、brackets（\(...\) / \[...\]）等 */
    delimiters?: string
    /** 传递给渲染引擎的附加选项 */
    katexOptions?: Record<string, unknown>
  }

  const texmath: (md: unknown, options?: TexmathOptions) => void

  export default texmath
}
