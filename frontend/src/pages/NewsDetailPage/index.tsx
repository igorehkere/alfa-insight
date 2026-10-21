type NewsDetailsPageProps = {
  newsId: string;
};

export const NewsDetailsPage = ({ newsId }: NewsDetailsPageProps) => {
  return (
    <div>
      <h2>Новость №{newsId}</h2>
    </div>
  );
};
