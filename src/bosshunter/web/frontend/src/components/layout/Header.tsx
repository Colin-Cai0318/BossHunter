import { useLocation } from 'react-router-dom'
import { Activity } from 'lucide-react'

const pageTitles: Record<string, string> = {
  '/': '工作台',
  '/jobs': '岗位池',
  '/monitor': '监测执行',
  '/config': '配置',
  '/resume-studio': '简历工作室',
}

export function Header() {
  const location = useLocation()
  const title = pageTitles[location.pathname] || 'BossHunter'

  return (
    <header className="h-16 shrink-0 border-b border-card-border bg-[#FFFCFA] flex items-center justify-between gap-3 px-3 sm:px-6">
      <p className="text-lg font-black text-foreground">{title}</p>
      <div className="flex items-center gap-2 text-xs text-muted">
        <Activity className="w-3 h-3 text-success" />
        <span>本地服务运行中</span>
      </div>
    </header>
  )
}
