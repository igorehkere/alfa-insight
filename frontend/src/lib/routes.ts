export const getDashboardPageRoute = () => '/'

export const getAnalyticsPageRoute = () => '/analytics'

export const getAnalyticsNewsUrl = (newsId: string) => `/analytics?news=${encodeURIComponent(newsId)}`