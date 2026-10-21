import { Route, BrowserRouter, Routes } from 'react-router-dom';
import * as routes from './lib/routes';
import { DashBoardPage } from './pages/DashBoardPage';
import { NewsFeedPage } from './pages/NewsFeedPage';

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path={routes.getDashboardPageRoute()} element={<DashBoardPage />} />
        <Route path={routes.getAnalyticsPageRoute()} element={<NewsFeedPage />} />
      </Routes>
    </BrowserRouter>
  );
}

export default App;
