import { Link } from 'react-router-dom';
import { getAnalyticsPageRoute } from '../../lib/routes';

export const DashBoardPage = () => {
  return (
    <div className="flex h-screen">
      <Link to={getAnalyticsPageRoute()}>Аналитика</Link>
    </div>
  );
};
