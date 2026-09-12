import { reactive } from 'vue'

export type PreviewRole = 'owner' | 'lead' | 'staff' | 'shareholder'
export type PreviewView =
    | 'home'
    | 'projects'
    | 'map'
    | 'knowledge'
    | 'overview'
    | 'finance'
    | 'tender'

export interface PreviewProject {
    id: string
    name: string
    area: string
    service: string
    fee: number
    households: number
    lead: string
    stage: string
    progress: number
    start: string
    end: string
    nextDate: string
    status: 'active' | 'completed'
}

// Only the registry comes from supplied workbooks. All project and financial
// records below are fictional, clearly labelled in the preview shell.
export const projects = reactive<PreviewProject[]>([
    {
        id: 'p1',
        name: '云栖里',
        area: '龙文区 · 碧湖街道',
        service: '首次业主大会 · 成立业委会',
        fee: 30000,
        households: 800,
        lead: '林悦',
        stage: '候选人公示',
        progress: 29,
        start: '2026-09-01',
        end: '2026-10-18',
        nextDate: '2026-09-13',
        status: 'active',
    },
    {
        id: 'p2',
        name: '澄湖花园',
        area: '芗城区 · 芝山街道',
        service: '业主大会 · 物业选聘议题',
        fee: 26000,
        households: 620,
        lead: '陈宁',
        stage: '业主投票',
        progress: 57,
        start: '2026-08-18',
        end: '2026-09-27',
        nextDate: '2026-09-20',
        status: 'active',
    },
    {
        id: 'p3',
        name: '南溪雅苑',
        area: '龙文区 · 蓝田街道',
        service: '首次业主大会 · 成立临委会',
        fee: 22000,
        households: 580,
        lead: '许安',
        stage: '筹备资料整理',
        progress: 0,
        start: '2026-09-08',
        end: '2026-10-26',
        nextDate: '2026-09-15',
        status: 'active',
    },
    {
        id: 'p4',
        name: '水岸名庭',
        area: '芗城区 · 西桥街道',
        service: '业主大会 · 日常议题表决',
        fee: 28000,
        households: 460,
        lead: '林悦',
        stage: '服务已完结',
        progress: 100,
        start: '2026-07-20',
        end: '2026-08-30',
        nextDate: '2026-08-30',
        status: 'completed',
    },
])

export const changeLog = reactive([
    {
        id: 'initial-1',
        projectId: 'p1',
        text: '候选人名单定稿及公示照片已关联，当前进入候选人公示阶段。',
        date: '2026-09-08 09:30',
        kind: '材料更新',
    },
    {
        id: 'initial-2',
        projectId: 'p3',
        text: '已建立筹备资料清单，主负责人为许安。',
        date: '2026-09-08 08:45',
        kind: '项目建立',
    },
])

// 示例完整毛利已计直接成本、节余奖金及抽成，独立记录，不由服务费固定推算。
export const operatingMonths = [
    {
        month: '1月',
        revenue: 0,
        gross: 0,
        fixed: 15000,
        received: 0,
        paid: 15000,
    },
    {
        month: '2月',
        revenue: 16000,
        gross: 11200,
        fixed: 15000,
        received: 0,
        paid: 19800,
    },
    {
        month: '3月',
        revenue: 22000,
        gross: 15400,
        fixed: 15000,
        received: 16000,
        paid: 21600,
    },
    {
        month: '4月',
        revenue: 28000,
        gross: 19600,
        fixed: 15500,
        received: 22000,
        paid: 23900,
    },
    {
        month: '5月',
        revenue: 30000,
        gross: 21000,
        fixed: 16000,
        received: 28000,
        paid: 25000,
    },
    {
        month: '6月',
        revenue: 52000,
        gross: 36400,
        fixed: 16000,
        received: 44000,
        paid: 31600,
    },
    {
        month: '7月',
        revenue: 48000,
        gross: 33600,
        fixed: 16500,
        received: 50000,
        paid: 30900,
    },
    {
        month: '8月',
        revenue: 56000,
        // 示例包含 600 元超出 30% 额度的直接成本，完整毛利相应降低。
        gross: 38600,
        fixed: 18000,
        received: 52000,
        paid: 34800,
    },
    {
        month: '9月',
        revenue: 58000,
        gross: 40600,
        fixed: 18000,
        received: 70000,
        paid: 35400,
    },
]

export function money(value: number): string {
    return new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 }).format(
        value,
    )
}

export function shortDate(value: string): string {
    const date = value.slice(0, 10).split('-')
    return `${Number(date[1])}月${Number(date[2])}日`
}
