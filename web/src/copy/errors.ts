export const errorCopy = {
    generic: '操作失败。',
    login: '用户名或密码不正确',
    unauthorized: '登录已失效，请重新登录。',
    forbidden: '没有权限',
    retry: '重试',
    unavailable: '操作失败。',
    request: '请求失败',
    agent: '霜月操作失败',
    audit: '审计记录加载失败',
    files: '文件操作失败',
    finance: '财务操作失败',
    knowledge: '知识库加载失败',
    projects: '项目操作失败',
    projectNameConflict: '项目名称已存在。',
    projectHasEntries: '项目下还有记账，无法删除。',
}

export function shortRequestId(id: string | undefined): string {
    if (!id) return ''
    return id.length > 8 ? id.slice(0, 8) : id
}
