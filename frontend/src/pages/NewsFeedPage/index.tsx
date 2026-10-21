import { useSearchParams } from 'react-router-dom';

import { NewsFeed } from '../../components/NewsFeed';
import { NewsDetailsPage } from '../NewsDetailPage';

export const NewsFeedPage = () => {
  const [searchParams, setSearchParams] = useSearchParams();

  const selectedNewsId = searchParams.get('news');

  const handleSelectNews = (newsId: string) => {
    setSearchParams((prev) => {
      const params = new URLSearchParams(prev);
      params.set('news', newsId);

      return params;
    });
  };

  return (
    <div className="flex h-screen">
      <div className="w-1/3 border-r p-4">
        <NewsFeed onSelectNews={handleSelectNews} />
      </div>

      <div className="flex-1 p-4">
        {selectedNewsId ? <NewsDetailsPage newsId={selectedNewsId} /> : <p>Выберите новость для просмотра аналитики</p>}
      </div>
    </div>
  );
};
