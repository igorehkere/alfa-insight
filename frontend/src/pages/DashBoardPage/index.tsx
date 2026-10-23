import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { AnimatePresence, motion } from 'motion/react';

import { getAnalyticsPageRoute } from '../../lib/routes';
import { CompanySelector } from '../../components/CompanySelector';

import css from './index.module.scss';

type Step = 'welcome' | 'company';

export const DashBoardPage = () => {
  const navigate = useNavigate();

  const [step, setStep] = useState<Step>('welcome');

  const handleContinue = (companyId: string, role: string) => {
    const params = new URLSearchParams({
      company: companyId,
      role,
    });

    navigate(`${getAnalyticsPageRoute()}?${params.toString()}`);
  };

  return (
    <div className={css.container}>
      <motion.div
        layout
        className={`${css.invite} ${step === 'company' ? css.inviteForm : ''}`}
        initial={{ y: '-100vh', opacity: 0 }}
        animate={{ y: 0, opacity: 1 }}
        transition={{
          duration: 0.9,
          ease: [0.22, 1, 0.36, 1],
          layout: {
            duration: 0.4,
          },
        }}
      >
        <AnimatePresence mode="wait" initial={false}>
          {step === 'welcome' ? (
            <motion.div
              key="welcome"
              className={css.welcomeContent}
              initial={{ opacity: 0, x: 0 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: -40 }}
              transition={{ duration: 0.25 }}
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
                <button type="button" className={css.butTry} onClick={() => setStep('company')}>
                  Попробовать
                </button>

                <button type="button" className={css.butLater}>
                  Позже
                </button>
              </div>
            </motion.div>
          ) : (
            <motion.div
              key="company"
              initial={{ opacity: 0, x: 40 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: 40 }}
              transition={{ duration: 0.25 }}
            >
              <h2 className={css.analytic}>Настройка аналитики</h2>

              <p className={css.info}>Выберите компанию, для которой хотите получать персональную аналитику.</p>

              <CompanySelector onBack={() => setStep('welcome')} onContinue={handleContinue} />
            </motion.div>
          )}
        </AnimatePresence>
      </motion.div>
    </div>
  );
};
