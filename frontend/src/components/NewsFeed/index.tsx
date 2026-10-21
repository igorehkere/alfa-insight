type NewsFeedProps = {
  onSelectNews: (newsId: string) => void;
};

export const NewsFeed = ({ onSelectNews }: NewsFeedProps) => {
  return (
    <div>
      <button onClick={() => onSelectNews('123')}>Изменение ключевой ставки</button>

      <button onClick={() => onSelectNews('456')}>Рост цен на сырьё</button>
    </div>
  );
};
