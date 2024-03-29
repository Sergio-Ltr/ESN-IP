import math
import torch
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy.fftpack import fft, ifft

from Reservoir import Reservoir
from ESN import EchoStateNetwork
from DATA import UNIFORM, TimeseriesDATA

"""
  Wrapper super class
"""
class Metric(): 
    def __init__(self, name: str = "Metric"):
        self.name = name
        super().__init__()
    
    def evaluate(self): 
        pass

"""
  Subclass grouping metrics requiring external data source to evaluate. 
"""
class EstrinsicMetric(Metric):
    def __init__(self, plot = False, name="Estrinsic Metric", data =TimeseriesDATA):
        self.plot = plot
        super().__init__(name)

    def evaluate(self, Y_pred: torch.Tensor, Y: torch.Tensor): 
        pass

    def fitting_plot(self, Y_pred: torch.Tensor, Y: torch.Tensor): 
        Y_pred_df = pd.DataFrame(Y_pred.numpy())
        Y_truth_df = pd.DataFrame(Y.numpy())
    
        ax = Y_truth_df.plot(grid=True, legend='Target', style=['g-','bo-','y^-'], linewidth=0.5 )
        Y_pred_df.plot(grid=True, legend='Reconstructed', style=['r--','bo-','y^-'], linewidth=0.25, ax=ax)
    
"""
  Normalized Root of Mean Square Error
"""
class NRMSE(EstrinsicMetric): 
    def __init__(self, plot = False, name="NRMSE"):
        super().__init__(plot, name)

    def evaluate(self, Y_pred: torch.Tensor, Y: torch.Tensor):
        if self.plot  == True: 
            self.fitting_plot(Y_pred, Y)
        return math.sqrt(torch.sum((Y_pred - Y)**2)/torch.var(Y))
        
"""
  Mean Squared Error 
"""
class MSE(EstrinsicMetric):
    def __init__(self, plot = False, name="MSE"):
        super().__init__(plot, name)

    def evaluate(self, Y_pred: torch.Tensor, Y: torch.Tensor):
        if self.plot == True: 
            self.fitting_plot(Y_pred, Y)
        
        #return float(torch.norm(X - Y, 2)/X.shape[0])
        return torch.mean((Y_pred - Y)**2) 

"""
  Mean Absolute Error
"""
class MAE(EstrinsicMetric): 
    def __init__(self, plot = False, name="MAE"):
        super().__init__(plot, name)

    def evaluate(self, Y_pred: torch.Tensor, Y: torch.Tensor):
        if self.plot == True: 
            self.fitting_plot(Y_pred, Y)

        return  torch.mean(abs(Y_pred - Y))

"""
  Mean Error
"""
class ME(EstrinsicMetric): 
    def __init__(self, plot = False, name="ME"):
        super().__init__(plot, name)

    def evaluate(self, Y_pred: torch.Tensor, Y: torch.Tensor): 
        if self.plot == True: 
            self.fitting_plot(Y_pred, Y)

        return  torch.mean(Y_pred - Y)
    
"""
  Subclass grouping metrics requiring more complex internal procedure than just propagate, train and compare. 
"""
class IntrinsicMetric(Metric):
    def __init__(self, name="Intrinsic Metric"):
        super().__init__(name)
    
    def evaluate(self, model: Reservoir): 
        pass 


"""
  Memory Capacity @TODO add reference paper. 
"""
class MC(IntrinsicMetric):
    def __init__(self, U=UNIFORM(size=1200), split_rate= [9, 0, 1], tau_max = 0, lambda_thikonov = 0, name="MC"):
        self.U = U
        self.split_rate = split_rate
        self.tau_max = tau_max
        self.lambda_thikonov = lambda_thikonov
        super().__init__(name)

    def evaluate(self, model: Reservoir):
        # def MemoryCapacity(self, l = 6000,  lambda_thikonov = 0, TR_SIZE = 5000, TS_SIZE = 1000): 
        # Take tau as the double of the Reservoir units, according to IP paper. 
        self.tau_max = model.N * 2 if self.tau_max == 0 else self.tau_max 
        mc = 0
        #sigma_U = torch.var(data.X_DATA)

        for tau in range(self.tau_max):
            self.U.delay_timeseries(tau)
            self.U.split(self.split_rate) 

            U_TR, Y_TR = self.U.TR()
            U_TS, Y_TS = self.U.TS()
            
            model.reset_initial_state()
            esn = EchoStateNetwork(model)
            esn.train(U_TR, Y_TR, self.lambda_thikonov, transient=100, verbose=False)

            X_TS = esn.predict(U_TS)

            target_mean = np.mean(Y_TS.numpy())
            output_mean = np.mean(X_TS.numpy()) 
 
            num, denom_t, denom_out = 0, 0, 0

            for i in range(len(X_TS)):
                deviat_t = Y_TS[i] - target_mean
                deviat_out = X_TS[i] - output_mean
                num += deviat_t * deviat_out
                denom_t += deviat_t**2
                denom_out += deviat_out**2
            num = num**2
            den = denom_t * denom_out
            mc += num/den

        #MC[k] = num/den
        #mc += TauMemoryCapacity().evaluate(U_TS, Y_TS)/sigma_U

        return mc
    
"""
  Lyapunov characteristic exponent, computed according to Gallicchio et al. in the paper "Local Lyapunov Exponent of Deep Echo State Networks". 
"""
class MLLE(IntrinsicMetric):
    def __init__(self,  U = UNIFORM(size = 1000, max_delay=0, split = False).X_FULL,  transient = 100, name="MLLE", ): 
        self.U = U
        self.transient = transient
        super().__init__(name)

    def evaluate(self, model: Reservoir):
        model.reset_initial_state()      
        model.warm_up(self.U[:self.transient])
        U = self.U[self.transient:None]

        eig_acc = 0
        W_rec = model.W_h * model.a if hasattr(model, "mask") else model.W_h
        N_s = U.shape[0]

        for t in range(N_s):
            model.predict(torch.Tensor(U[t:t+1]))
            D = torch.diag(1 - model.h_t**2).numpy()
            eig_k, _ = np.linalg.eig(D*W_rec.numpy())
            eig_acc += np.log(np.absolute(eig_k))

        return max(eig_acc/N_s)
    
"""
  Deviation from linearity, measure proposed by Verstraeten et al. in the paper "Memory versus Non-Linearity in Reservoirs".
"""
class DeltaPhi(IntrinsicMetric):
    def __init__(self, theta_range = (np.linspace(0.01, 0.5, 100)*200).astype(int), starttime = 0.0, endtime = 2.0, steps = 1000, verbose=False, plot=False, name="DeltaPhi"):
        self.theta_range = theta_range
        self.starttime = starttime
        self.endtime = endtime
        self.steps = steps

        self.verbose = verbose
        self.plot = plot
        super().__init__(name)


    def evaluate(self, model: Reservoir):
      de_acc = 0
      t = np.linspace(self.starttime, self.endtime, num=self.steps)

      for theta in self.theta_range:

        f = np.sin(2*np.pi*theta*t) 

        f_res = model.predict(f).numpy()
        f_res -= np.mean(f_res, axis=0)
        f_res = np.mean(f_res, axis=1)

        fhat = np.fft.fft(f_res)
        N = len(fhat)

        halvedfhat = fhat[:int(N/2)]
        powspec = abs(halvedfhat)**2

        fs = self.steps/(self.endtime - self.starttime)

        freq = np.linspace(0,int(fs/2),int(N/2))

        de_fi_theta = 1 - powspec[2*theta]/np.sum(powspec)

        if self.verbose: 
          print(f"Frequence:{theta}, Deviation: {de_fi_theta},  Powerspect: {powspec[2*theta]}, Total Energy: {np.sum(powspec)}") 

        if self.plot:
          plt.plot(freq,powspec)
          plt.xlim([0,100])

        de_acc += de_fi_theta

      return de_acc/len(self.theta_range)

"""
  Number of Effective Dimensions @TODO add reference paper. 
"""   
class Neff(IntrinsicMetric): 
    def __init__(self, U = UNIFORM(split = False).X_FULL, transient = 0, name="Neff"): 
        self.U = U
        self.transient = transient
        super().__init__(name)
        
    def evaluate(self, model: Reservoir):
      model.reset_initial_state()
      model.warm_up(self.U[:self.transient])
      activation_covariance = np.cov(model.predict(self.U[self.transient:None]).T) #building the covariance matrix
      eigs = np.linalg.eig(activation_covariance)[0] #compute eigenvalues
      return np.sum(eigs)**2/np.sum(eigs**2) #compute metric
    
class Rho(IntrinsicMetric): 
    def __init__(self):
        super().__init__("Rho")
        
    def evaluate(self, model: Reservoir):
        return model.rho()
    
class KL(IntrinsicMetric): 
    def __init__(self, U=torch.tensor([]), transient=100, is_input=True):
        self.U = U
        self.transient = transient
        self.is_input = is_input
        super().__init__("KL")

    def evaluate(self, model: Reservoir,):
          if hasattr(model, "mask"): 
            if self.U.shape[0] == 0:
                print(model.loss)
                return model.loss
            else:
                return model.evalaute_loss(self.U, self.transient, self.is_input)
          else: 
              return 0