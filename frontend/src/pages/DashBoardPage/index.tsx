import { Link } from 'react-router-dom';
import { getAnalyticsPageRoute } from '../../lib/routes';
import css from './index.module.scss';
import { motion } from 'motion/react';

export const DashBoardPage = () => {
  return (
    <div className={css.container}>
      <motion.div
        className={css.invite}
        initial={{ y: '-100vh', opacity: 0 }}
        animate={{ y: 0, opacity: 1 }}
        transition={{
          duration: 0.9,
          ease: [0.22, 1, 0.36, 1],
        }}
      >
        <div>
          <div className={css.newFunc}>
            <p>НОВАЯ ФУНКЦИЯ:</p>
          </div>
          <p className={css.analytic}>Персональная аналитика событий</p>
        </div>
        <p className={css.info}>
          Узнайте, как последние новости могут повлиять на Вашу компанию и какие действия предпринять.
        </p>
        <div className={css.buttons}>
          <Link to={getAnalyticsPageRoute()}>
            <button className={css.butTry}>
              Попробовать
            </button>
          </Link>
          <button className={css.butLater}>Позже</button>
        </div>
      </motion.div>
    </div>
  );
};
